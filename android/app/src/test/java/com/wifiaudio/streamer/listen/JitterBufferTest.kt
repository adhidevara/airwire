package com.wifiaudio.streamer.listen

import com.wifiaudio.streamer.codec.FrameDecoder
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.PriorityQueue

/** Mirrors receiver/tests/test_jitter_buffer.py so both buffers behave the same. */
class JitterBufferTest {

    /** Decodes packet [k] to a frame filled with k; PLC gives zeros; FEC gives -1. */
    private class FakeDecoder : FrameDecoder {
        override val sampleRate = 48_000
        override val channels = 2
        var resets = 0

        override fun decode(packet: ByteArray?, length: Int, out: ShortArray, frameSize: Int, fec: Boolean): Int {
            val n = if (packet == null || fec) minOf(frameSize, 480) else 480
            val value: Short = when {
                packet == null -> 0
                fec -> -1
                else -> packet[0].toShort()
            }
            out.fill(value, 0, n * channels)
            return n
        }

        override fun reset() {
            resets++
        }

        override fun close() = Unit
    }

    private var now = 1000.0
    private val decoder = FakeDecoder()
    private val out = ShortArray(JitterBuffer.MAX_FRAME_SAMPLES * 2)

    private fun make(initialDepth: Int = 2) = JitterBuffer(decoder, initialDepth = initialDepth, clock = { now })

    private fun packet(seq: Long) = byteArrayOf((seq % 100).toByte())

    private fun JitterBuffer.push(vararg seqs: Long, redundancy: Boolean = true) {
        for (s in seqs) push(s, packet(s), if (redundancy && s > 0) packet(s - 1) else null)
    }

    @Test
    fun prerollWaitsForTarget() {
        val jb = make()
        assertEquals(0, jb.pop(out))
        jb.push(0)
        assertEquals(0, jb.pop(out))
        jb.push(1)
        assertEquals(480, jb.pop(out))
        assertTrue(jb.playing)
        assertEquals(0.toShort(), out[0]) // frame 0
    }

    @Test
    fun reorderedPacketsPlayInOrder() {
        val jb = make()
        jb.push(0, 2, 1, 3)
        val played = (0 until 4).map { jb.pop(out); out[0].toInt() }
        assertEquals(listOf(0, 1, 2, 3), played)
        val s = jb.stats()
        assertEquals(0L, s.recovered + s.fec + s.concealed + s.underruns)
    }

    @Test
    fun singleLossIsRecoveredFromRedundantCopy() {
        val jb = make()
        jb.push(0, 1, 3, 4)
        repeat(5) { jb.pop(out) }
        val s = jb.stats()
        assertEquals(1L, s.recovered)
        assertEquals(0L, s.fec + s.concealed)
    }

    @Test
    fun lossWithoutRedundancyUsesFecThenPlc() {
        val jb = make()
        jb.push(0, 1, 4, redundancy = false) // 2 -> PLC (3 missing), 3 -> FEC from 4
        repeat(5) { jb.pop(out) }
        val s = jb.stats()
        assertEquals(1L, s.concealed)
        assertEquals(1L, s.fec)
    }

    @Test
    fun underrunConcealsKeepsTimelineAndRecordsLateness() {
        val jb = make()
        jb.push(0, 1)
        jb.pop(out); jb.pop(out)
        assertEquals(480, jb.pop(out)) // nothing buffered: PLC
        assertEquals(1L, jb.stats().underruns)
        now += 0.015
        jb.push(2)
        jb.pop(out)
        assertEquals(2.toShort(), out[0]) // frame 2 played from its own packet, not skipped
        assertEquals(3L, jb.stats().played)
    }

    @Test
    fun stallGoesSilentAndPrerollsAgain() {
        val jb = make()
        jb.push(0, 1)
        jb.pop(out); jb.pop(out)
        val results = (0..JitterBuffer.STALL_FRAMES).map { jb.pop(out) }
        assertTrue(results.dropLast(1).all { it > 0 })
        assertEquals(0, results.last())
        assertTrue(!jb.playing)
        jb.push(10)
        assertEquals(0, jb.pop(out)) // pre-roll again
    }

    @Test
    fun lateAndDuplicatePacketsAreCounted() {
        val jb = make()
        jb.push(0, 1, 2)
        jb.pop(out); jb.pop(out)
        jb.push(0)
        jb.push(2)
        val s = jb.stats()
        assertEquals(1L, s.late)
        assertEquals(1L, s.duplicates)
    }

    // ---- timing simulations (same scenarios as the Python tests) -----------------------

    private fun simulate(jb: JitterBuffer, arrivals: List<Double>, popStart: Double, popPeriod: Double = 0.010) {
        data class Event(val t: Double, val kind: Int, val seq: Int)
        val events = PriorityQueue(compareBy<Event>({ it.t }, { it.kind }))
        arrivals.forEachIndexed { s, t -> events.add(Event(t, 0, s)) }
        val pops = ((arrivals.max() - popStart) / popPeriod).toInt()
        for (i in 0 until pops) events.add(Event(popStart + i * popPeriod, 1, i))
        val base = now
        while (events.isNotEmpty()) {
            val e = events.poll()!!
            now = base + e.t
            if (e.kind == 0) jb.push(e.seq.toLong()) else jb.pop(out)
        }
    }

    @Test
    fun surplusLatencyIsCutOnACalmNetwork() {
        val jb = make()
        simulate(jb, List(800) { it * 0.010 }, popStart = 0.100)
        val s = jb.stats()
        assertTrue("dropped ${s.dropped}", s.dropped >= 8)
        assertEquals(0L, s.underruns)
        assertTrue("wait ${jb.waitMs}", jb.waitMs < 15)
    }

    @Test
    fun burstyCaptureKeepsOneBurstOfBuffer() {
        val jb = make()
        simulate(jb, List(800) { (it / 2 * 2 + 1) * 0.010 }, popStart = 0.150)
        assertEquals(0L, jb.stats().underruns)
        assertTrue("wait ${jb.waitMs}", jb.waitMs < 25)
    }

    @Test
    fun recurringStallsKeepEnoughLatency() {
        val jb = make()
        val arrivals = List(1200) { s ->
            val t = s * 0.010
            val stallStart = Math.floor(t / 2.0) * 2.0 + 1.0
            if (t >= stallStart && t < stallStart + 0.050) stallStart + 0.050 else t
        }
        simulate(jb, arrivals, popStart = 0.120)
        val s = jb.stats()
        assertTrue(s.dropped > 0)
        assertEquals(0L, s.underruns)
        assertTrue("wait ${jb.waitMs}", jb.waitMs > 40)
    }

    @Test
    fun resetCanKeepWhatItLearned() {
        val jb = make()
        simulate(jb, List(300) { it * 0.010 }, popStart = 0.050)
        val learned = jb.historySize
        assertTrue(learned > 0)
        jb.reset(keepHistory = true)
        assertEquals(learned, jb.historySize)
        assertEquals(0, jb.pop(out)) // the buffer itself is empty: pre-roll again
        jb.reset()
        assertEquals(0, jb.historySize)
    }

    @Test
    fun clockDriftIsCompensated() {
        val jb = make()
        simulate(jb, List(6000) { it * 0.00999 }, popStart = 0.030)
        val s = jb.stats()
        assertEquals(0L, s.underruns)
        assertTrue("dropped ${s.dropped}", s.dropped >= 5)
        assertTrue("wait ${jb.waitMs}", jb.waitMs < 30)
    }
}
