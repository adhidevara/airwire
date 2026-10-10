package com.wifiaudio.streamer.files

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.wifiaudio.streamer.MainActivity
import com.wifiaudio.streamer.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.launch

/**
 * Keeps the process in the foreground while files are moving, so a transfer survives the
 * user leaving the app. The work itself runs in [FileTransfers].
 */
class TransferService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var watcher: Job? = null
    private var lastNotifyAt = 0L

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, build(FileTransfers.transfers.value),
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
            )
        } catch (e: Exception) {
            Log.w(TAG, "cannot enter the foreground", e)
            stopSelf()
            return START_NOT_STICKY
        }
        if (watcher == null) {
            watcher = scope.launch {
                combine(FileTransfers.transfers, FileTransfers.pairState) { list, pair ->
                    // Uploads waiting for pairing don't hold the service.
                    val busy = list.any { it.state == TransferState.RUNNING } ||
                        (pair is PairState.Paired && list.any { it.state == TransferState.QUEUED })
                    busy to list
                }.collect { (busy, list) ->
                    if (!busy) {
                        ServiceCompat.stopForeground(this@TransferService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                        stopSelf()
                    } else {
                        val now = SystemClock.elapsedRealtime()
                        if (now - lastNotifyAt >= 500) {
                            lastNotifyAt = now
                            getSystemService(NotificationManager::class.java)?.notify(NOTIFICATION_ID, build(list))
                        }
                    }
                }
            }
        }
        return START_NOT_STICKY
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android 15+ caps dataSync services at 6 h a day.
        stopSelf()
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    private fun build(list: List<FileTransfer>): Notification {
        getSystemService(NotificationManager::class.java)?.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "File transfers", NotificationManager.IMPORTANCE_LOW),
        )
        val active = list.filter { it.active }
        val running = active.firstOrNull { it.state == TransferState.RUNNING }
        val total = active.sumOf { it.size.coerceAtLeast(0) }
        val done = active.sumOf { it.bytes }
        val title = when {
            running == null -> "Waiting to transfer files"
            active.size == 1 -> if (running.direction == Direction.TO_PC) "Sending ${running.name}" else "Receiving ${running.name}"
            else -> "Transferring ${active.size} files"
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_stream)
            .setContentTitle(title)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setProgress(100, if (total > 0) (done * 100 / total).toInt() else 0, total <= 0)
            .setContentIntent(open)
            .build()
    }

    companion object {
        private const val TAG = "TransferService"
        private const val CHANNEL_ID = "transfers"
        private const val NOTIFICATION_ID = 2

        fun start(context: Context) {
            try {
                ContextCompat.startForegroundService(context, Intent(context, TransferService::class.java))
            } catch (e: Exception) {
                // Not allowed from the background on Android 12+; the transfer still runs.
                Log.w(TAG, "cannot start transfer service: ${e.message}")
            }
        }
    }
}
