package com.wifiaudio.streamer.listen

import com.wifiaudio.streamer.codec.FrameDecoder
import com.wifiaudio.streamer.codec.OpusException
import java.util.Collections
import kotlin.math.ceil
import kotlin.math.min
import kotlin.math.roundToInt

/**
 * Adaptive jitter buffer, the same algorithm as the PC receiver (receiver/airwire/jitter_buffer.py).
 *
 * The network thread calls [push] for every AUDIO packet; the playback thread calls [pop]
 * once per frame it needs.
 *
 * - Pre-roll: nothing plays until [target] frames are buffered.
 * - Missing frame: redundant copy, then Opus in-band FEC from the next packet, then PLC.
 * - Underrun: play one PLC frame without advancing (the timeline moves one frame later).
 *   After [STALL_FRAMES] underruns in a row, go silent and pre-roll again.
 * - Latency control: every played frame records its slack, how long its data waited before
 *   it was needed (negative when it arrived late). If every frame of the last [window] waited
 *   longer than one frame plus [marginMs], two frames are merged into one with a crossfade.
 */
class JitterBuffer(
    private val decoder: FrameDecoder,
    initialDepth: Int = 2,
    private val minDepth: Int = 1,
    private val maxDepth: Int = 15,
    private val window: Int = 1000,
    private val lateQuantile: Double = 0.0,
    marginMs: Double = 2.0,
    private val clock: () -> Double = { System.nanoTime() / 1e9 },
) {
    data class Stats(
        var played: Long = 0,
        var recovered: Long = 0,
        var fec: Long = 0,
        var concealed: Long = 0,
        var underruns: Long = 0,
        var late: Long = 0,
        var duplicates: Long = 0,
        var dropped: Long = 0,
        var resets: Long = 0,
    )

    private val lock = Any()
    private val channels = decoder.channels
    private val sampleRate = decoder.sampleRate
    private val margin = marginMs / 1000
    private var frameSamples = sampleRate / 100 // until the first packet says otherwise
    private val scratch = ShortArray(MAX_FRAME_SAMPLES * channels)
    private val stats = Stats()

    @Volatile var target: Int = initialDepth
        private set

    // Buffer state, cleared on every stall.
    private val packets = HashMap<Long, ByteArray>()
    private val recovered = HashMap<Long, ByteArray>()
    private val avail = HashMap<Long, Double>()          // seq -> when its data arrived
    private val missed = HashMap<Long, DoubleArray>()    // concealed seq -> [time, offset]
    private var next = NONE
    private var highest = NONE
    @Volatile var playing = false
        private set
    private var stall = 0
    private var waiting = false
    private var waitingSince = 0.0
    private var waitingOffset = 0.0

    // Slack statistics, cleared on reset(). Stored as slack + offset (see the Python version).
    private val slackFifo = ArrayDeque<Double>()
    private val slackSorted = ArrayList<Double>()
    private var offset = 0.0
    private var sinceDrop = 0
    @Volatile private var avgWait = 0.0
    @Volatile private var avgDepth = 0.0

    private val frameS get() = frameSamples.toDouble() / sampleRate
    val frameMs: Double get() = frameSamples * 1000.0 / sampleRate
    val bufferMs: Double get() = avgDepth * frameMs
    val targetMs: Double get() = target * frameMs
    val waitMs: Double get() = avgWait * 1000

    fun stats(): Stats = synchronized(lock) { stats.copy() }

    /** Slack samples held; for tests. */
    internal val historySize: Int get() = synchronized(lock) { slackSorted.size }

    /**
     * Forget everything buffered (new session / sender gone). Keeps [target]. [keepHistory]
     * also keeps the slack statistics: the stream came back over the same Wi-Fi.
     */
    fun reset(keepHistory: Boolean = false) = synchronized(lock) {
        clear()
        if (!keepHistory) clearSlack()
        decoder.reset()
        avgDepth = 0.0
        stats.resets++
    }

    private fun clear() {
        packets.clear()
        recovered.clear()
        avail.clear()
        missed.clear()
        next = NONE
        highest = NONE
        playing = false
        stall = 0
        waiting = false
    }

    private fun clearSlack() {
        slackFifo.clear()
        slackSorted.clear()
        offset = 0.0
        sinceDrop = 0
        avgWait = 0.0
    }

    private fun depth(): Int =
        if (next == NONE || highest == NONE || highest < next) 0 else (highest - next + 1).toInt()

    // ---- slack statistics ---------------------------------------------------------------

    private fun record(slack: Double, at: Double = offset, weight: Int = 1) {
        val value = slack + at
        repeat(weight) {
            slackFifo.addLast(value)
            val i = Collections.binarySearch(slackSorted, value)
            slackSorted.add(if (i < 0) -i - 1 else i, value)
            if (slackFifo.size > window) {
                val j = Collections.binarySearch(slackSorted, slackFifo.removeFirst())
                if (j >= 0) slackSorted.removeAt(j)
            }
        }
    }

    private fun recordLate(lateness: Double, at: Double) {
        val weight = min(MAX_LATE_WEIGHT, 1 + ceil(lateness / frameS).toInt())
        record(-lateness, at, weight)
    }

    private fun lowSlack(): Double? {
        val n = slackSorted.size
        if (n < MIN_SAMPLES) return null
        return slackSorted[(n * lateQuantile).toInt()] - offset
    }

    // ---- producer -----------------------------------------------------------------------

    fun push(seq: Long, primary: ByteArray, redundant: ByteArray?) {
        val now = clock()
        synchronized(lock) {
            if (next == NONE) {
                next = seq
            } else if (seq < next) {
                if (playing) {
                    stats.late++
                    missed.remove(seq)?.let { recordLate(now - it[0], it[1]) }
                    return
                }
                next = seq // out-of-order during pre-roll: start earlier
            }
            if (packets.containsKey(seq)) {
                stats.duplicates++
                return
            }
            if (waiting) {
                recordLate(now - waitingSince, waitingOffset)
                waiting = false
            }
            packets[seq] = primary
            recovered.remove(seq)
            avail.putIfAbsent(seq, now)
            val prev = seq - 1
            if (redundant != null && prev >= next && !packets.containsKey(prev)) {
                recovered.putIfAbsent(prev, redundant)
                avail.putIfAbsent(prev, now)
            }
            if (highest == NONE || seq > highest) highest = seq
        }
    }

    // ---- consumer -----------------------------------------------------------------------

    /**
     * Writes the next frame into [out] (interleaved, [channels] channels) and returns its
     * samples per channel, or 0 for silence. [out] must hold [MAX_FRAME_SAMPLES] per channel.
     */
    fun pop(out: ShortArray): Int {
        val now = clock()
        synchronized(lock) {
            if (next == NONE) return 0
            val depth = depth()
            if (!playing) {
                if (depth < target) return 0
                playing = true
            }
            if (depth == 0) return underrun(now, out)

            stall = 0
            avgDepth += (depth - avgDepth) / 32
            catchUp(depth)

            var n = takeNext(now, out)
            sinceDrop++
            val low = lowSlack() ?: return n
            updateTarget(low)
            if (shouldDrop(low) && depth() > 0) {
                // Shift first: the merged-in frame's slack already reflects the cut.
                offset += frameS
                val m = takeNext(now, scratch)
                n = crossfade(out, n, scratch, m)
                stats.dropped++
                sinceDrop = 0
            }
            return n
        }
    }

    private fun underrun(now: Double, out: ShortArray): Int {
        stats.underruns++
        stall++
        if (stall == 1) {
            waiting = true
            waitingSince = now
            waitingOffset = offset
        }
        if (stall > STALL_FRAMES) {
            // Pre-roll again at `target`; undo the concealment shifts and remember the stall.
            val since = if (waiting) waitingSince else now
            val at = if (waiting) waitingOffset else offset
            offset = at
            recordLate(now - since, at)
            clear()
            decoder.reset()
            avgDepth = 0.0
            return 0
        }
        stats.concealed++
        offset -= frameS // the timeline just moved one frame later
        return conceal(out)
    }

    /** After a burst (e.g. Wi-Fi hiccup) skip straight to [target] frames behind. */
    private fun catchUp(depth: Int) {
        if (depth <= maxDepth * 2) return
        val newNext = highest - target + 1
        stats.dropped += newNext - next
        offset += (newNext - next) * frameS
        next = newNext
        packets.keys.removeAll { it < newNext }
        recovered.keys.removeAll { it < newNext }
        avail.keys.removeAll { it < newNext }
    }

    private fun updateTarget(low: Double) {
        val surplusFrames = (low - margin) / frameS
        val ideal = (avgDepth - surplusFrames).roundToInt()
        target = ideal.coerceIn(minDepth, maxDepth)
    }

    private fun shouldDrop(low: Double): Boolean {
        if (low < frameS + margin) return false
        // Up to ~12% faster while far above the target, ~5% when close to it.
        return sinceDrop >= if (low >= 3 * frameS) 8 else 20
    }

    private fun takeNext(now: Double, dst: ShortArray): Int {
        val seq = next
        next = seq + 1
        stats.played++
        val packet = packets.remove(seq)
        val copy = recovered.remove(seq)
        avail.remove(seq)?.let { arrived ->
            val slack = now - arrived
            record(slack)
            avgWait += (slack - avgWait) / 32
        }
        if (packet != null) return decodeInto(packet, dst)
        if (copy != null) {
            stats.recovered++
            return decodeInto(copy, dst)
        }
        missed[seq] = doubleArrayOf(now, offset)
        if (missed.size > 64) missed.keys.removeAll { it <= seq - 64 }
        val following = packets[seq + 1]
        if (following != null) {
            stats.fec++
            return try {
                decoder.decode(following, following.size, dst, frameSamples, fec = true)
            } catch (e: OpusException) {
                conceal(dst)
            }
        }
        stats.concealed++
        return conceal(dst)
    }

    private fun decodeInto(packet: ByteArray, dst: ShortArray): Int {
        val n = try {
            decoder.decode(packet, packet.size, dst, MAX_FRAME_SAMPLES)
        } catch (e: OpusException) {
            stats.concealed++
            return conceal(dst)
        }
        if (n > 0) frameSamples = n
        return n
    }

    private fun conceal(dst: ShortArray): Int = decoder.decode(null, 0, dst, frameSamples)

    private fun crossfade(a: ShortArray, na: Int, b: ShortArray, nb: Int): Int {
        val n = min(na, nb)
        if (n == 0) {
            System.arraycopy(b, 0, a, 0, nb * channels)
            return nb
        }
        val last = (n - 1).coerceAtLeast(1).toFloat()
        for (i in 0 until n) {
            val up = i / last
            for (c in 0 until channels) {
                val k = i * channels + c
                a[k] = (a[k] * (1f - up) + b[k] * up).roundToInt().coerceIn(-32768, 32767).toShort()
            }
        }
        return n
    }

    companion object {
        const val STALL_FRAMES = 5
        const val MIN_SAMPLES = 100
        const val MAX_LATE_WEIGHT = 20
        const val MAX_FRAME_SAMPLES = 5760 // 120 ms at 48 kHz, the longest Opus frame
        private const val NONE = -1L
    }
}
