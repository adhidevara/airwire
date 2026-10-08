package com.wifiaudio.streamer

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

enum class AudioSourceType { INTERNAL, MICROPHONE }

sealed interface StreamStatus {
    data object Idle : StreamStatus
    data class Starting(val target: String, val source: AudioSourceType) : StreamStatus
    data class Streaming(val target: String, val source: AudioSourceType) : StreamStatus
    data class Failed(val message: String) : StreamStatus
}

val StreamStatus.isActive: Boolean
    get() = this is StreamStatus.Starting || this is StreamStatus.Streaming

data class StreamStats(
    val packetsSent: Long = 0,
    val kbps: Int = 0,
    /** True while PINGs arrive from the receiver (it hears us and answers). */
    val receiverResponding: Boolean = false,
    val receiverLossPercent: Float = 0f,
    val receiverJitterMs: Float = 0f,
    val receiverBufferMs: Int = 0,
)

/** Process-wide state shared by the foreground service and the UI. */
object StreamController {
    private val _status = MutableStateFlow<StreamStatus>(StreamStatus.Idle)
    val status: StateFlow<StreamStatus> = _status.asStateFlow()

    private val _stats = MutableStateFlow(StreamStats())
    val stats: StateFlow<StreamStats> = _stats.asStateFlow()

    /** Read by the streaming thread before every frame, so slider changes apply live. */
    @Volatile
    var bitrateKbps: Int = 128

    fun setStatus(status: StreamStatus) {
        _status.value = status
    }

    fun setStats(stats: StreamStats) {
        _stats.value = stats
    }
}
