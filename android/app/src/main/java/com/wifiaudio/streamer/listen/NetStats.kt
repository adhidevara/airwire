package com.wifiaudio.streamer.listen

import kotlin.math.abs

/**
 * Loss, RFC 3550 jitter, RTT and rate of the incoming stream (port of receiver/airwire/stats.py).
 * Times are in seconds from the caller's clock; not thread-safe, the caller synchronises.
 */
class NetStats(private val frameS: Double = 0.010) {
    var received = 0L
        private set
    var loss = 0.0
        private set
    var kbps = 0.0
        private set
    var rttMs: Double? = null
        private set
    private var jitterS = 0.0
    private var bytes = 0L
    private var baseSeq = -1L
    private var maxSeq = -1L
    private var lastTransit: Double? = null
    private var markTime = Double.NaN
    private var markExpected = 0L
    private var markReceived = 0L
    private var markBytes = 0L

    val jitterMs: Double get() = jitterS * 1000

    private val expected: Long get() = if (baseSeq < 0) 0 else maxSeq - baseSeq + 1

    fun reset() {
        received = 0; bytes = 0; baseSeq = -1; maxSeq = -1; lastTransit = null
        jitterS = 0.0; rttMs = null; loss = 0.0; kbps = 0.0
        markTime = Double.NaN; markExpected = 0; markReceived = 0; markBytes = 0
    }

    fun onAudio(seq: Long, size: Int, now: Double) {
        received++
        bytes += size
        when {
            baseSeq < 0 -> { baseSeq = seq; maxSeq = seq }
            seq > maxSeq -> maxSeq = seq
            seq < baseSeq -> baseSeq = seq
        }
        // The frame counter is the sender clock.
        val transit = now - seq * frameS
        lastTransit?.let { jitterS += (abs(transit - it) - jitterS) / 16 }
        lastTransit = transit
    }

    fun onPong(sentNs: Long, nowNs: Long) {
        val rtt = (nowNs - sentNs) / 1e6
        if (rtt < 0) return
        rttMs = rttMs?.let { it + (rtt - it) / 4 } ?: rtt
    }

    /** Closes the current measurement interval (every ~0.5 s). */
    fun sample(now: Double) {
        if (markTime.isNaN()) {
            markTime = now
            return
        }
        val elapsed = now - markTime
        if (elapsed <= 0) return
        val dExpected = expected - markExpected
        val dReceived = received - markReceived
        if (dExpected > 0) loss = ((dExpected - dReceived).toDouble() / dExpected).coerceIn(0.0, 1.0)
        kbps = (bytes - markBytes) * 8 / 1000.0 / elapsed
        markTime = now
        markExpected = expected
        markReceived = received
        markBytes = bytes
    }
}
