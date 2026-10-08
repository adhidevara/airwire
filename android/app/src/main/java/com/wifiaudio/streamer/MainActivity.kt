package com.wifiaudio.streamer

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
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
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wifiaudio.streamer.net.Protocol
import com.wifiaudio.streamer.service.StreamingService
import com.wifiaudio.streamer.ui.StreamerScreen
import com.wifiaudio.streamer.ui.AirwireTheme

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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            AirwireTheme {
                val ui by vm.ui.collectAsStateWithLifecycle()
                val status by StreamController.status.collectAsStateWithLifecycle()
                val stats by StreamController.stats.collectAsStateWithLifecycle()
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

                StreamerScreen(
                    ui = ui,
                    status = status,
                    stats = stats,
                    snackbarHostState = snackbar,
                    onHostChange = vm::setHost,
                    onPortChange = vm::setPort,
                    onSourceChange = vm::setSource,
                    onBitrateChange = vm::setBitrate,
                    onScan = vm::scan,
                    onPickReceiver = vm::pickReceiver,
                    onDismissScan = vm::dismissScan,
                    onToggle = ::onToggle,
                )
            }
        }
    }

    private fun onToggle() {
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
