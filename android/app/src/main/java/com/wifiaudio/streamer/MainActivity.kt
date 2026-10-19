package com.wifiaudio.streamer

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.core.content.ContextCompat
import androidx.core.content.IntentCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wifiaudio.streamer.files.FileTransfer
import com.wifiaudio.streamer.files.FileTransfers
import com.wifiaudio.streamer.files.PairState
import com.wifiaudio.streamer.listen.ListenController
import com.wifiaudio.streamer.listen.ListenService
import com.wifiaudio.streamer.listen.isActive
import com.wifiaudio.streamer.net.Protocol
import com.wifiaudio.streamer.service.StreamingService
import com.wifiaudio.streamer.ui.AirwireApp
import com.wifiaudio.streamer.ui.AirwireTheme
import com.wifiaudio.streamer.ui.AudioScreen
import com.wifiaudio.streamer.ui.FilesScreen

class MainActivity : ComponentActivity() {

    private val vm: StreamViewModel by viewModels()

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
            // Notifications are optional; only the audio permission blocks streaming.
            if (hasPermission(Manifest.permission.RECORD_AUDIO)) {
                requestCaptureAndStart()
            } else {
                vm.showMessage("Audio permission is needed to capture sound.")
            }
        }

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val projectionLauncher =
        registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
            val data = result.data
            if (result.resultCode == RESULT_OK && data != null) {
                launchStreaming(result.resultCode, data)
            } else {
                vm.showMessage(
                    "Internal audio capture was not allowed.",
                    actionLabel = "Use microphone",
                    action = UiAction.USE_MICROPHONE,
                )
            }
        }

    private val filePicker =
        registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            if (uris.isNotEmpty()) FileTransfers.send(uris)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        FileTransfers.init(this)
        if (savedInstanceState == null) handleShare(intent)
        setContent {
            AirwireTheme {
                val ui by vm.ui.collectAsStateWithLifecycle()
                val tab by vm.tab.collectAsStateWithLifecycle()
                val status by StreamController.status.collectAsStateWithLifecycle()
                val stats by StreamController.stats.collectAsStateWithLifecycle()
                val listenStatus by ListenController.status.collectAsStateWithLifecycle()
                val listenStats by ListenController.stats.collectAsStateWithLifecycle()
                val pairing by vm.pairing.collectAsStateWithLifecycle()
                val pairState by FileTransfers.pairState.collectAsStateWithLifecycle()
                val transfers by FileTransfers.transfers.collectAsStateWithLifecycle()
                val snackbar = remember { SnackbarHostState() }

                LaunchedEffect(ui.message?.id) {
                    val message = ui.message ?: return@LaunchedEffect
                    val result = snackbar.showSnackbar(
                        message = message.text,
                        actionLabel = message.actionLabel,
                        withDismissAction = message.actionLabel != null,
                        duration = if (message.actionLabel != null) SnackbarDuration.Long else SnackbarDuration.Short,
                    )
                    vm.consumeMessage(message.id)
                    if (result == SnackbarResult.ActionPerformed && message.action == UiAction.USE_MICROPHONE) {
                        useMicrophone()
                    }
                }

                AirwireApp(
                    tab = tab,
                    onTabChange = vm::selectTab,
                    snackbarHostState = snackbar,
                    audio = {
                        AudioScreen(
                            ui = ui,
                            status = status,
                            stats = stats,
                            listenStatus = listenStatus,
                            listenStats = listenStats,
                            paired = pairing != null,
                            onModeChange = vm::setMode,
                            onHostChange = vm::setHost,
                            onPortChange = vm::setPort,
                            onSourceChange = vm::setSource,
                            onBitrateChange = vm::setBitrate,
                            onScan = vm::scan,
                            onPickReceiver = vm::pickReceiver,
                            onDismissScan = vm::dismissScan,
                            onToggle = ::onToggle,
                            onGoToFiles = { vm.selectTab(AppTab.FILES) },
                        )
                    },
                    files = {
                        FilesScreen(
                            pairState = pairState,
                            transfers = transfers,
                            onPair = { code ->
                                askForNotifications()
                                FileTransfers.pair(code)
                            },
                            onUnpair = FileTransfers::unpair,
                            onPickFiles = ::pickFiles,
                            onOpen = ::openFile,
                            onClearFinished = FileTransfers::clearFinished,
                            onGoToAudio = { vm.selectTab(AppTab.AUDIO) },
                        )
                    },
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleShare(intent)
    }

    override fun onStart() {
        super.onStart()
        FileTransfers.setVisible(true)
    }

    override fun onStop() {
        FileTransfers.setVisible(false)
        super.onStop()
    }

    // ---- files ------------------------------------------------------------------

    /** "Share → Airwire" from another app. */
    private fun handleShare(intent: Intent?) {
        val uris: List<Uri> = when (intent?.action) {
            Intent.ACTION_SEND ->
                listOfNotNull(IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java))
            Intent.ACTION_SEND_MULTIPLE ->
                IntentCompat.getParcelableArrayListExtra(intent, Intent.EXTRA_STREAM, Uri::class.java).orEmpty()
            else -> return
        }
        vm.selectTab(AppTab.FILES)
        if (uris.isEmpty()) {
            vm.showMessage("Only files can be sent to the PC.")
            return
        }
        FileTransfers.send(uris)
        if (FileTransfers.pairState.value !is PairState.Paired) {
            vm.showMessage("Pair with your PC to send ${if (uris.size == 1) "this file" else "these files"}.")
        }
    }

    private fun pickFiles() {
        askForNotifications()
        filePicker.launch(arrayOf("*/*"))
    }

    private fun openFile(transfer: FileTransfer) {
        val uri = transfer.uri ?: return
        val view = Intent(Intent.ACTION_VIEW).setDataAndType(uri, transfer.mime)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        try {
            startActivity(view)
        } catch (e: ActivityNotFoundException) {
            vm.showMessage("No app on this phone can open ${transfer.name}.")
        }
    }

    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            !hasPermission(Manifest.permission.POST_NOTIFICATIONS)
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    // ---- audio ------------------------------------------------------------------

    private fun onToggle() {
        if (vm.ui.value.mode == AudioMode.LISTEN) {
            onToggleListen()
            return
        }
        if (ListenController.status.value.isActive) {
            vm.showMessage("Stop listening to the PC first.")
            return
        }
        if (StreamController.status.value.isActive) {
            StreamingService.stop(this)
            return
        }
        vm.validationError()?.let {
            vm.showMessage(it)
            return
        }
        val missing = requiredPermissions().filterNot(::hasPermission)
        if (missing.isEmpty()) requestCaptureAndStart() else permissionLauncher.launch(missing.toTypedArray())
    }

    /** PC -> phone. Needs the file-transfer pairing, which authenticates the request. */
    private fun onToggleListen() {
        if (ListenController.status.value.isActive) {
            ListenService.stop(this)
            return
        }
        if (StreamController.status.value.isActive) {
            vm.showMessage("Stop streaming to the PC first.")
            return
        }
        vm.validationError()?.let {
            vm.showMessage(it)
            return
        }
        val token = vm.pairing.value?.token
        if (token == null) {
            vm.showMessage("Pair with your PC in the Files tab first.")
            return
        }
        askForNotifications()
        val s = vm.ui.value
        ListenService.start(this, s.host, s.port.toIntOrNull() ?: Protocol.DEFAULT_PORT, token)
    }

    private fun requiredPermissions(): List<String> = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun hasPermission(permission: String) =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

    /** Internal audio needs the system screen-capture consent dialog, every session. */
    private fun requestCaptureAndStart() {
        if (vm.ui.value.source != AudioSourceType.INTERNAL) {
            launchStreaming(null, null)
            return
        }
        val manager = getSystemService(MediaProjectionManager::class.java)!!
        val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            // Whole-device capture, so every app's audio is included.
            manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
        } else {
            manager.createScreenCaptureIntent()
        }
        projectionLauncher.launch(intent)
    }

    private fun launchStreaming(resultCode: Int?, data: Intent?) {
        val s = vm.ui.value
        val port = s.port.toIntOrNull() ?: Protocol.DEFAULT_PORT
        StreamingService.start(this, s.host, port, s.source, resultCode, data)
    }

    private fun useMicrophone() {
        vm.setSource(AudioSourceType.MICROPHONE)
        onToggle()
    }
}
