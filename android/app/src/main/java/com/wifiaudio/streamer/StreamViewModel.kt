package com.wifiaudio.streamer

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.wifiaudio.streamer.data.FilePairing
import com.wifiaudio.streamer.data.SettingsRepository
import com.wifiaudio.streamer.data.StreamSettings
import com.wifiaudio.streamer.listen.ListenController
import com.wifiaudio.streamer.net.DiscoveredReceiver
import com.wifiaudio.streamer.net.Protocol
import com.wifiaudio.streamer.net.ReceiverDiscovery
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.flow.updateAndGet
import kotlinx.coroutines.launch

enum class UiAction { USE_MICROPHONE }

enum class AppTab(val label: String) { AUDIO("Audio"), FILES("Files") }

/** SEND: this phone's audio plays on the PC. LISTEN: the PC's audio plays on this phone. */
enum class AudioMode { SEND, LISTEN }

data class UiMessage(
    val text: String,
    val actionLabel: String? = null,
    val action: UiAction? = null,
    val id: Long = System.nanoTime(),
)

data class UiState(
    val host: String = "",
    val port: String = Protocol.DEFAULT_PORT.toString(),
    val source: AudioSourceType = AudioSourceType.INTERNAL,
    val bitrateKbps: Int = 128,
    val mode: AudioMode = AudioMode.SEND,
    val scanning: Boolean = false,
    /** Result of the last scan, shown as a dialog; null when there is nothing to show. */
    val found: List<DiscoveredReceiver>? = null,
    val message: UiMessage? = null,
)

class StreamViewModel(app: Application) : AndroidViewModel(app) {
    private val repo = SettingsRepository(app)
    private val _ui = MutableStateFlow(UiState())
    val ui: StateFlow<UiState> = _ui.asStateFlow()

    private val _tab = MutableStateFlow(AppTab.AUDIO)
    val tab: StateFlow<AppTab> = _tab.asStateFlow()

    /** File-transfer pairing; listening to the PC uses the same token. */
    val pairing: StateFlow<FilePairing?> = repo.pairing.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    fun selectTab(tab: AppTab) {
        _tab.value = tab
    }

    init {
        viewModelScope.launch {
            val s = repo.settings.first()
            _ui.update {
                it.copy(
                    host = s.host, port = s.port.toString(), source = s.source,
                    bitrateKbps = s.bitrateKbps, mode = s.mode,
                )
            }
            StreamController.bitrateKbps = s.bitrateKbps
            ListenController.bitrateKbps = s.bitrateKbps
        }
    }

    fun setHost(value: String) = edit { it.copy(host = value.trim()) }

    fun setPort(value: String) = edit { it.copy(port = value.filter(Char::isDigit).take(5)) }

    fun setSource(value: AudioSourceType) = edit { it.copy(source = value) }

    fun setMode(value: AudioMode) = edit { it.copy(mode = value) }

    fun setBitrate(kbps: Int) {
        val clamped = kbps.coerceIn(MIN_KBPS, MAX_KBPS)
        if (clamped == _ui.value.bitrateKbps) return // the slider repeats values while dragging
        StreamController.bitrateKbps = clamped
        ListenController.bitrateKbps = clamped
        edit { it.copy(bitrateKbps = clamped) }
    }

    /** Null when the form can start a stream, otherwise what to fix. */
    fun validationError(): String? {
        val s = _ui.value
        if (s.host.isBlank()) return "Enter the laptop IP address or tap Auto Scan Receiver."
        val port = s.port.toIntOrNull()
        if (port == null || port !in 1..65535) return "Port must be between 1 and 65535."
        return null
    }

    fun scan() {
        if (_ui.value.scanning) return
        val port = _ui.value.port.toIntOrNull() ?: Protocol.DEFAULT_PORT
        _ui.update { it.copy(scanning = true, found = null) }
        viewModelScope.launch {
            val result = runCatching { ReceiverDiscovery.scan(getApplication<Application>(), port) }
            _ui.update { it.copy(scanning = false, found = result.getOrNull()) }
            result.exceptionOrNull()?.let { showMessage("Scan failed: ${it.message}") }
        }
    }

    fun pickReceiver(receiver: DiscoveredReceiver) =
        edit { it.copy(host = receiver.host, port = receiver.port.toString(), found = null) }

    fun dismissScan() = _ui.update { it.copy(found = null) }

    fun showMessage(text: String, actionLabel: String? = null, action: UiAction? = null) =
        _ui.update { it.copy(message = UiMessage(text, actionLabel, action)) }

    fun consumeMessage(id: Long) =
        _ui.update { if (it.message?.id == id) it.copy(message = null) else it }

    private fun edit(transform: (UiState) -> UiState) {
        val s = _ui.updateAndGet(transform)
        viewModelScope.launch {
            repo.save(
                StreamSettings(
                    host = s.host,
                    port = s.port.toIntOrNull() ?: Protocol.DEFAULT_PORT,
                    source = s.source,
                    bitrateKbps = s.bitrateKbps,
                    mode = s.mode,
                ),
            )
        }
    }

    companion object {
        const val MIN_KBPS = 64
        const val MAX_KBPS = 192
        const val STEP_KBPS = 16
    }
}
