package com.wifiaudio.streamer.files

import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Environment
import android.os.SystemClock
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.util.Log
import android.webkit.MimeTypeMap
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.wifiaudio.streamer.R
import com.wifiaudio.streamer.data.FilePairing
import com.wifiaudio.streamer.data.SettingsRepository
import com.wifiaudio.streamer.deviceName
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File
import java.io.IOException
import java.util.UUID

enum class Direction { TO_PC, FROM_PC }

enum class TransferState { QUEUED, RUNNING, DONE, FAILED }

data class FileTransfer(
    val id: String,
    val direction: Direction,
    val name: String,
    val size: Long,
    val bytes: Long = 0,
    val state: TransferState = TransferState.QUEUED,
    val error: String? = null,
    /** Source file for TO_PC, the saved file for FROM_PC once it is done. */
    val uri: Uri? = null,
    val mime: String = "application/octet-stream",
) {
    val progress: Float get() = if (size <= 0) 0f else (bytes.toFloat() / size).coerceIn(0f, 1f)
    val active: Boolean get() = state == TransferState.QUEUED || state == TransferState.RUNNING
}

sealed interface PairState {
    /** No PC address yet (set it on the Audio tab). */
    data object NoHost : PairState
    data class Unpaired(val host: String, val error: String? = null) : PairState
    data class Pairing(val host: String) : PairState
    data class Paired(val pcName: String, val host: String) : PairState
}

/**
 * File transfer with the PC (receiver/airwire/files.py). Process-wide, so transfers keep
 * going while the activity is recreated; [TransferService] keeps the process in the
 * foreground while anything is moving.
 *
 * Phone -> PC: [send] queues uploads that run one at a time once paired.
 * PC -> phone: while the app is visible or streaming, the PC's outbox is long-polled and
 * new files are saved to Download/Airwire.
 */
object FileTransfers {
    private const val TAG = "FileTransfers"
    private const val CHANNEL_FILES = "files"
    private const val POLL_WAIT_S = 25
    private const val RETRY_MS = 3_000L
    private const val PROGRESS_INTERVAL_MS = 150L

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var app: Context
    private lateinit var repo: SettingsRepository
    private var started = false

    private val _transfers = MutableStateFlow<List<FileTransfer>>(emptyList())
    val transfers: StateFlow<List<FileTransfer>> = _transfers.asStateFlow()

    private val _pairState = MutableStateFlow<PairState>(PairState.NoHost)
    val pairState: StateFlow<PairState> = _pairState.asStateFlow()

    private val visible = MutableStateFlow(false)
    private val streaming = MutableStateFlow(false)
    private val pairingBusy = MutableStateFlow(false)
    private val pairingError = MutableStateFlow<String?>(null)
    private val target = MutableStateFlow<Target?>(null)
    private val uploads = Channel<String>(Channel.UNLIMITED)

    private data class Target(val host: String, val port: Int, val pairing: FilePairing?) {
        fun client() = FileTransferClient(host, port, pairing?.token)
    }

    fun init(context: Context) {
        synchronized(this) {
            if (started) return
            started = true
        }
        app = context.applicationContext
        repo = SettingsRepository(app)
        scope.launch {
            combine(repo.settings, repo.pairing, pairingBusy, pairingError) { s, pairing, busy, error ->
                val host = s.host.trim()
                target.value = if (host.isBlank()) null else Target(host, s.port, pairing)
                when {
                    host.isBlank() -> PairState.NoHost
                    busy -> PairState.Pairing(host)
                    pairing != null -> PairState.Paired(pairing.pcName.ifBlank { host }, host)
                    else -> PairState.Unpaired(host, error)
                }
            }.collect { _pairState.value = it }
        }
        scope.launch { uploadWorker() }
        scope.launch { inboxWorker() }
    }

    /** Polling for files from the PC runs only while the UI is visible or audio is streaming. */
    fun setVisible(value: Boolean) {
        visible.value = value
    }

    fun setStreaming(value: Boolean) {
        streaming.value = value
    }

    // ---- pairing ------------------------------------------------------------------

    fun pair(code: String) {
        val t = target.value ?: return
        if (!pairingBusy.compareAndSet(expect = false, update = true)) return
        pairingError.value = null
        scope.launch {
            try {
                val result = FileTransferClient(t.host, t.port, null).pair(code, app.deviceName())
                repo.savePairing(FilePairing(result.token, result.pcName))
            } catch (e: HttpStatusException) {
                pairingError.value = when (e.code) {
                    403 -> "Wrong code. Use the code shown in the Airwire window on your PC."
                    429 -> "Too many wrong codes. Wait a minute and try again."
                    else -> e.message
                }
            } catch (e: IOException) {
                pairingError.value = "Can't reach ${t.host}:${t.port}. Is the Airwire receiver running?"
            } finally {
                pairingBusy.value = false
            }
        }
    }

    fun unpair() {
        scope.launch { repo.clearPairing() }
    }

    // ---- phone -> PC --------------------------------------------------------------

    fun send(uris: List<Uri>) {
        if (uris.isEmpty()) return
        scope.launch {
            for (uri in uris) {
                val (name, size) = describe(uri)
                val transfer = FileTransfer(
                    id = UUID.randomUUID().toString(), direction = Direction.TO_PC,
                    name = name, size = size, uri = uri, mime = mimeFor(name),
                )
                _transfers.update { listOf(transfer) + it }
                uploads.send(transfer.id)
            }
            TransferService.start(app)
        }
    }

    private suspend fun uploadWorker() {
        for (id in uploads) {
            val transfer = _transfers.value.firstOrNull { it.id == id } ?: continue
            val tgt = target.filterNotNull().first { it.pairing != null }
            upload(tgt, transfer)
        }
    }

    private fun upload(tgt: Target, transfer: FileTransfer) {
        val uri = transfer.uri ?: return
        set(transfer.id) { it.copy(state = TransferState.RUNNING, bytes = 0, error = null) }
        var temp: File? = null
        try {
            val resolver = app.contentResolver
            var size = transfer.size
            if (size < 0) {
                // Unknown length: HTTP needs Content-Length, so stage the file locally first.
                temp = File.createTempFile("upload", null, app.cacheDir).also { file ->
                    resolver.openInputStream(uri)?.use { input -> file.outputStream().use { input.copyTo(it) } }
                        ?: throw IOException("Can't open the file")
                }
                size = temp.length()
                set(transfer.id) { it.copy(size = size) }
            }
            val input = temp?.inputStream() ?: resolver.openInputStream(uri) ?: throw IOException("Can't open the file")
            val progress = throttled(transfer.id)
            val savedAs = input.use { tgt.client().upload(transfer.name, size, it, progress) }
            set(transfer.id) { it.copy(state = TransferState.DONE, bytes = size, name = savedAs) }
        } catch (e: HttpStatusException) {
            if (e.code == 401) unpairAfterRevoke()
            fail(transfer.id, e)
        } catch (e: Exception) {
            Log.w(TAG, "upload of ${transfer.name} failed", e)
            fail(transfer.id, e)
        } finally {
            temp?.delete()
        }
    }

    // ---- PC -> phone --------------------------------------------------------------

    private suspend fun inboxWorker() {
        var since = -1L
        val saved = mutableSetOf<String>() // downloaded, maybe not yet acknowledged
        while (true) {
            val tgt = combine(target, visible, streaming) { t, v, s ->
                t?.takeIf { it.pairing != null && (v || s) }
            }.filterNotNull().first()
            val client = tgt.client()
            try {
                val outbox = client.outbox(since, POLL_WAIT_S)
                since = outbox.version
                for (item in outbox.files) {
                    if (item.id !in saved) {
                        if (!download(client, tgt, item)) continue
                        saved += item.id
                    }
                    runCatching { client.acknowledge(item.id) }
                }
            } catch (e: HttpStatusException) {
                if (e.code == 401) unpairAfterRevoke() else delay(RETRY_MS)
            } catch (e: Exception) {
                Log.d(TAG, "outbox poll failed: ${e.message}")
                delay(RETRY_MS)
            }
        }
    }

    private fun download(client: FileTransferClient, tgt: Target, item: OutboxItem): Boolean {
        val mime = mimeFor(item.name)
        upsert(FileTransfer(item.id, Direction.FROM_PC, item.name, item.size, state = TransferState.RUNNING, mime = mime))
        TransferService.start(app)
        val resolver = app.contentResolver
        val pending = ContentValues().apply {
            put(MediaStore.MediaColumns.DISPLAY_NAME, item.name)
            put(MediaStore.MediaColumns.MIME_TYPE, mime)
            put(MediaStore.MediaColumns.RELATIVE_PATH, "${Environment.DIRECTORY_DOWNLOADS}/Airwire")
            put(MediaStore.MediaColumns.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, pending)
        if (uri == null) {
            fail(item.id, IOException("Can't create the file in Downloads"))
            return false
        }
        return try {
            resolver.openOutputStream(uri)?.use { out -> client.download(item.id, out, throttled(item.id)) }
                ?: throw IOException("Can't write to Downloads")
            resolver.update(uri, ContentValues().apply { put(MediaStore.MediaColumns.IS_PENDING, 0) }, null, null)
            set(item.id) { it.copy(state = TransferState.DONE, bytes = item.size, uri = uri) }
            notifyReceived(item.id, item.name, uri, mime, tgt.pairing?.pcName.orEmpty())
            true
        } catch (e: Exception) {
            Log.w(TAG, "download of ${item.name} failed", e)
            runCatching { resolver.delete(uri, null, null) }
            if (e is HttpStatusException && e.code == 401) unpairAfterRevoke()
            fail(item.id, e)
            false
        }
    }

    // ---- helpers ------------------------------------------------------------------

    fun clearFinished() {
        _transfers.update { list -> list.filter { it.active } }
    }

    private fun unpairAfterRevoke() {
        scope.launch {
            repo.clearPairing()
            pairingError.value = "The PC reset its pairing. Enter the new code."
        }
    }

    private fun set(id: String, change: (FileTransfer) -> FileTransfer) {
        _transfers.update { list -> list.map { if (it.id == id) change(it) else it } }
    }

    private fun upsert(transfer: FileTransfer) {
        _transfers.update { list -> listOf(transfer) + list.filterNot { it.id == transfer.id } }
    }

    private fun fail(id: String, e: Exception) {
        set(id) { it.copy(state = TransferState.FAILED, error = e.message ?: e.javaClass.simpleName) }
    }

    /** Progress callback that updates the UI state at most every [PROGRESS_INTERVAL_MS]. */
    private fun throttled(id: String): (Long) -> Unit {
        var last = 0L
        return { bytes ->
            val now = SystemClock.elapsedRealtime()
            if (now - last >= PROGRESS_INTERVAL_MS) {
                last = now
                set(id) { it.copy(bytes = bytes) }
            }
        }
    }

    private fun describe(uri: Uri): Pair<String, Long> {
        var name: String? = null
        var size = -1L
        runCatching {
            app.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
                ?.use { c ->
                    if (c.moveToFirst()) {
                        name = c.getString(0)
                        if (!c.isNull(1)) size = c.getLong(1)
                    }
                }
        }
        return (name ?: uri.lastPathSegment ?: "file") to size
    }

    fun mimeFor(name: String): String {
        val ext = name.substringAfterLast('.', "").lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "application/octet-stream"
    }

    @SuppressLint("MissingPermission") // checked via areNotificationsEnabled()
    private fun notifyReceived(id: String, name: String, uri: Uri, mime: String, pcName: String) {
        val manager = NotificationManagerCompat.from(app)
        if (!manager.areNotificationsEnabled()) return
        app.getSystemService(NotificationManager::class.java)?.createNotificationChannel(
            NotificationChannel(CHANNEL_FILES, "Received files", NotificationManager.IMPORTANCE_DEFAULT),
        )
        val open = Intent(Intent.ACTION_VIEW).setDataAndType(uri, mime)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        val tap = PendingIntent.getActivity(app, id.hashCode(), open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val notification = NotificationCompat.Builder(app, CHANNEL_FILES)
            .setSmallIcon(R.drawable.ic_stat_stream)
            .setContentTitle("Received $name")
            .setContentText(if (pcName.isNotBlank()) "From $pcName · Download/Airwire" else "Saved to Download/Airwire")
            .setColor(ContextCompat.getColor(app, R.color.ic_launcher_background))
            .setContentIntent(tap)
            .setAutoCancel(true)
            .build()
        runCatching { manager.notify(id.hashCode(), notification) }
    }
}
