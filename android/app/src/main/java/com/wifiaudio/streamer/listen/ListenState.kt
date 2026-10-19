package com.wifiaudio.streamer.listen

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** PC -> phone: this phone plays what the PC is playing. */
sealed interface ListenStatus {
    data object Idle : ListenStatus
    data class Connecting(val target: String) : ListenStatus
    data class Playing(val target: String, val pcName: String) : ListenStatus
    data class Failed(val message: String) : ListenStatus
}

val ListenStatus.isActive: Boolean
    get() = this is ListenStatus.Connecting || this is ListenStatus.Playing

data class ListenStats(
    val packets: Long = 0,
    val kbps: Int = 0,
    val lossPercent: Float = 0f,
    val jitterMs: Float = 0f,
    val bufferMs: Int = 0,
    val rttMs: Float? = null,
    /** Phone-side estimate: one way + buffer + frame + output; excludes the PC's capture. */
    val latencyMs: Int? = null,
    val concealed: Long = 0,
    /** True while audio keeps arriving. */
    val receiving: Boolean = false,
)

/** Process-wide state shared by [ListenService] and the UI. */
object ListenController {
    private val _status = MutableStateFlow<ListenStatus>(ListenStatus.Idle)
    val status: StateFlow<ListenStatus> = _status.asStateFlow()

    private val _stats = MutableStateFlow(ListenStats())
    val stats: StateFlow<ListenStats> = _stats.asStateFlow()

    /** Sent to the PC with every LISTEN, so slider changes apply within a second. */
    @Volatile
    var bitrateKbps: Int = 128

    fun setStatus(status: ListenStatus) {
        _status.value = status
    }

    fun setStats(stats: ListenStats) {
        _stats.value = stats
    }
}
