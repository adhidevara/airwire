package com.wifiaudio.streamer.service

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.media.AudioRecord
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import com.wifiaudio.streamer.AudioSourceType
import com.wifiaudio.streamer.MainActivity
import com.wifiaudio.streamer.R
import com.wifiaudio.streamer.StreamController
import com.wifiaudio.streamer.StreamStats
import com.wifiaudio.streamer.StreamStatus
import com.wifiaudio.streamer.audio.AudioCapture
import com.wifiaudio.streamer.deviceName
import com.wifiaudio.streamer.files.FileTransfers
import com.wifiaudio.streamer.net.UdpStreamer

/**
 * Foreground service that owns the capture session, so streaming survives a locked screen
 * and the app being in the background. The ongoing notification is the privacy indicator.
 */
class StreamingService : Service() {

    private val main = Handler(Looper.getMainLooper())
    private var streamer: UdpStreamer? = null
    private var projection: MediaProjection? = null
    private val locks by lazy { NetworkLocks(this, "wifiaudio:stream") }
    private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> if (streamer == null) start(intent)
            else -> shutdown(error = null)
        }
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        shutdown(error = null)
        super.onDestroy()
    }

    private fun start(intent: Intent) {
        stopping = false
        val host = intent.getStringExtra(EXTRA_HOST).orEmpty()
        val port = intent.getIntExtra(EXTRA_PORT, 0)
        val source = intent.getStringExtra(EXTRA_SOURCE)
            ?.let { runCatching { AudioSourceType.valueOf(it) }.getOrNull() }
            ?: AudioSourceType.MICROPHONE
        val target = "$host:$port"

        // startForeground must come first: before getMediaProjection() on Android 14+, and
        // always before the service may stop, or the system kills the app.
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, buildNotification(source, target), foregroundTypes(source),
            )
        } catch (e: Exception) {
            shutdown("Cannot start the streaming service: ${e.message}")
            return
        }
        StreamController.setStatus(StreamStatus.Starting(target, source))

        try {
            check(hasRecordPermission()) { "Audio recording permission is missing" }
            val record = when (source) {
                AudioSourceType.INTERNAL -> AudioCapture.internal(obtainProjection(intent))
                AudioSourceType.MICROPHONE -> AudioCapture.microphone()
            }
            if (record.state != AudioRecord.STATE_INITIALIZED) {
                record.release()
                error("Audio capture could not be initialised")
            }
            locks.acquire()
            streamer = UdpStreamer(
                record = record,
                channels = if (source == AudioSourceType.INTERNAL) 2 else 1,
                host = host,
                port = port,
                source = source,
                deviceName = deviceName(),
                onStats = StreamController::setStats,
                onFatal = { message -> main.post { shutdown(message) } },
            ).also { it.start() }
            StreamController.setStatus(StreamStatus.Streaming(target, source))
            // Files from the PC keep arriving while streaming, even with the app closed.
            FileTransfers.init(this)
            FileTransfers.setStreaming(true)
        } catch (e: Exception) {
            shutdown(e.message ?: e.javaClass.simpleName)
        }
    }

    private fun foregroundTypes(source: AudioSourceType): Int {
        // The microphone type only exists from Android 11; Android 10 needs no type for it.
        val mic = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
        return when (source) {
            AudioSourceType.INTERNAL -> ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION or mic
            AudioSourceType.MICROPHONE -> mic
        }
    }

    private fun obtainProjection(intent: Intent): MediaProjection {
        val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, 0)
        val data = IntentCompat.getParcelableExtra(intent, EXTRA_RESULT_DATA, Intent::class.java)
            ?: error("Screen-capture consent is missing")
        val manager = getSystemService(MediaProjectionManager::class.java)!!
        val mp = manager.getMediaProjection(resultCode, data)
            ?: error("Screen-capture consent was not granted")
        // Fires when the user taps "Stop sharing" in the system UI.
        mp.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                main.post { shutdown(error = null) }
            }
        }, main)
        projection = mp
        return mp
    }

    private fun shutdown(error: String?) {
        if (stopping) return
        stopping = true
        streamer?.stop()
        streamer = null
        projection?.let {
            projection = null
            runCatching { it.stop() }
        }
        locks.release()
        FileTransfers.setStreaming(false)
        StreamController.setStats(StreamStats())
        StreamController.setStatus(if (error != null) StreamStatus.Failed(error) else StreamStatus.Idle)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun hasRecordPermission() =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    private fun buildNotification(source: AudioSourceType, target: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)!!
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Audio streaming", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while audio from this phone is being streamed"
            },
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 1, Intent(this, StreamingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        val (title, text) = when (source) {
            AudioSourceType.INTERNAL ->
                "Streaming internal audio" to "Everything this phone plays is sent to $target"
            AudioSourceType.MICROPHONE ->
                "Streaming microphone" to "The microphone is live and sent to $target"
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_stream)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .build()
    }

    companion object {
        private const val ACTION_START = "com.wifiaudio.streamer.action.START"
        private const val ACTION_STOP = "com.wifiaudio.streamer.action.STOP"
        private const val EXTRA_HOST = "host"
        private const val EXTRA_PORT = "port"
        private const val EXTRA_SOURCE = "source"
        private const val EXTRA_RESULT_CODE = "projection_result_code"
        private const val EXTRA_RESULT_DATA = "projection_result_data"
        private const val CHANNEL_ID = "streaming"
        private const val NOTIFICATION_ID = 1

        /** [projectionData] is the consent result; required for [AudioSourceType.INTERNAL]. */
        fun start(
            context: Context,
            host: String,
            port: Int,
            source: AudioSourceType,
            projectionResultCode: Int? = null,
            projectionData: Intent? = null,
        ) {
            val intent = Intent(context, StreamingService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_HOST, host)
                .putExtra(EXTRA_PORT, port)
                .putExtra(EXTRA_SOURCE, source.name)
            if (projectionResultCode != null && projectionData != null) {
                intent.putExtra(EXTRA_RESULT_CODE, projectionResultCode)
                intent.putExtra(EXTRA_RESULT_DATA, projectionData)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, StreamingService::class.java).setAction(ACTION_STOP))
        }
    }
}
