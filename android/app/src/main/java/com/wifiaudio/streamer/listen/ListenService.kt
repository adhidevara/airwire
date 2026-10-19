package com.wifiaudio.streamer.listen

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.wifiaudio.streamer.MainActivity
import com.wifiaudio.streamer.R
import com.wifiaudio.streamer.deviceName
import com.wifiaudio.streamer.files.FileTransfers
import com.wifiaudio.streamer.service.NetworkLocks

/**
 * Foreground service that plays the PC's audio, so it keeps going with the screen locked.
 * Holds audio focus like any media player: it ducks for notifications, goes quiet during a
 * call and stops when another app starts playing.
 */
class ListenService : Service() {

    private val main = Handler(Looper.getMainLooper())
    private var listener: PcAudioListener? = null
    private var focusRequest: AudioFocusRequest? = null
    private val locks by lazy { NetworkLocks(this, "wifiaudio:listen") }
    private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> if (listener == null) start(intent)
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
        val token = intent.getStringExtra(EXTRA_TOKEN).orEmpty()
        val target = "$host:$port"
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, buildNotification(target),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK,
            )
        } catch (e: Exception) {
            shutdown("Cannot start playback: ${e.message}")
            return
        }
        ListenController.setStatus(ListenStatus.Connecting(target))
        try {
            check(token.isNotBlank()) { "Pair with the PC in the Files tab first." }
            check(requestFocus()) { "Another app is using audio right now (a call?)." }
            locks.acquire()
            listener = PcAudioListener(
                host = host,
                port = port,
                token = token,
                deviceName = deviceName(),
                onStatus = { status ->
                    ListenController.setStatus(status)
                    if (status is ListenStatus.Playing) main.post { updateNotification(status.pcName) }
                },
                onStats = ListenController::setStats,
                onFatal = { message -> main.post { shutdown(message) } },
            ).also { it.start() }
            // Files from the PC keep arriving while listening, like while streaming.
            FileTransfers.init(this)
            FileTransfers.setStreaming(true)
        } catch (e: Exception) {
            shutdown(e.message ?: e.javaClass.simpleName)
        }
    }

    private fun requestFocus(): Boolean {
        val audio = getSystemService(AudioManager::class.java)!!
        val request = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build(),
            )
            .setOnAudioFocusChangeListener({ change -> onFocusChange(change) }, main)
            .build()
        focusRequest = request
        return audio.requestAudioFocus(request) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
    }

    private fun onFocusChange(change: Int) {
        when (change) {
            AudioManager.AUDIOFOCUS_GAIN -> listener?.setVolume(1f)
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK -> listener?.setVolume(DUCK_VOLUME)
            AudioManager.AUDIOFOCUS_LOSS_TRANSIENT -> listener?.setVolume(0f)
            AudioManager.AUDIOFOCUS_LOSS -> shutdown("Stopped: another app started playing audio.")
        }
    }

    private fun shutdown(error: String?) {
        if (stopping) return
        stopping = true
        listener?.stop()
        listener = null
        focusRequest?.let { getSystemService(AudioManager::class.java)?.abandonAudioFocusRequest(it) }
        focusRequest = null
        locks.release()
        FileTransfers.setStreaming(false)
        ListenController.setStats(ListenStats())
        ListenController.setStatus(if (error != null) ListenStatus.Failed(error) else ListenStatus.Idle)
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun updateNotification(pcName: String) {
        getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, buildNotification(pcName))
    }

    private fun buildNotification(source: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)!!
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Playing PC audio", NotificationManager.IMPORTANCE_LOW).apply {
                description = "Shown while this phone plays audio from your PC"
            },
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        val stop = PendingIntent.getService(
            this, 2, Intent(this, ListenService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_stream)
            .setContentTitle("Playing PC audio")
            .setContentText("From $source")
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(open)
            .addAction(0, "Stop", stop)
            .build()
    }

    companion object {
        private const val ACTION_START = "com.wifiaudio.streamer.action.LISTEN"
        private const val ACTION_STOP = "com.wifiaudio.streamer.action.STOP_LISTEN"
        private const val EXTRA_HOST = "host"
        private const val EXTRA_PORT = "port"
        private const val EXTRA_TOKEN = "token"
        private const val CHANNEL_ID = "listening"
        private const val NOTIFICATION_ID = 3
        private const val DUCK_VOLUME = 0.3f

        fun start(context: Context, host: String, port: Int, token: String) {
            val intent = Intent(context, ListenService::class.java)
                .setAction(ACTION_START)
                .putExtra(EXTRA_HOST, host)
                .putExtra(EXTRA_PORT, port)
                .putExtra(EXTRA_TOKEN, token)
            ContextCompat.startForegroundService(context, intent)
        }

        fun stop(context: Context) {
            context.startService(Intent(context, ListenService::class.java).setAction(ACTION_STOP))
        }
    }
}
