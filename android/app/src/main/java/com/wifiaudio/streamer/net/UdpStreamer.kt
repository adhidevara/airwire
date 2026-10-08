package com.wifiaudio.streamer.net

import android.media.AudioRecord
import android.os.Process
import android.os.SystemClock
import android.util.Log
import com.wifiaudio.streamer.AudioSourceType
import com.wifiaudio.streamer.StreamController
import com.wifiaudio.streamer.StreamStats
import com.wifiaudio.streamer.audio.AudioCapture
import com.wifiaudio.streamer.codec.OpusEncoder
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread
import kotlin.random.Random

/**
 * Capture → Opus → UDP, one frame at a time with no queue in between.
 *
 * The audio thread owns [record] and the encoder: it starts recording, streams, and on exit
 * sends BYE and releases everything. A second thread answers the receiver's PINGs on the
 * same socket and publishes stats.
 */
class UdpStreamer(
    private val record: AudioRecord,
    private val channels: Int,
    private val host: String,
    private val port: Int,
    private val source: AudioSourceType,
    private val deviceName: String,
    private val onStats: (StreamStats) -> Unit,
    private val onFatal: (String) -> Unit,
) {
    private val session = Random.nextInt(1, 0x10000)
    private val socket = DatagramSocket().apply { soTimeout = 250 }
    private val packetsSent = AtomicLong()
    private val bytesSent = AtomicLong()

    @Volatile private var running = false
    @Volatile private var lastPing: Protocol.Ping? = null
    @Volatile private var lastPingAt = 0L

    private var audioThread: Thread? = null
    private var controlThread: Thread? = null

    fun start() {
        running = true
        audioThread = thread(name = "wifiaudio-stream") { runAudio() }
        controlThread = thread(name = "wifiaudio-control") { runControl() }
    }

    /** Blocks briefly (≤ ~1 frame) while the audio thread finishes and sends BYE. */
    fun stop() {
        running = false
        audioThread?.join(STOP_TIMEOUT_MS)
        controlThread?.join(STOP_TIMEOUT_MS)
        socket.close()
    }

    private fun runAudio() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        var target: InetSocketAddress? = null
        try {
            val address = InetSocketAddress(InetAddress.getByName(host), port)
            target = address
            OpusEncoder(AudioCapture.SAMPLE_RATE, channels, StreamController.bitrateKbps * 1000).use { encoder ->
                record.startRecording()
                streamLoop(address, encoder)
            }
        } catch (e: Exception) {
            Log.e(TAG, "streaming stopped", e)
            if (running) onFatal(e.message ?: e.javaClass.simpleName)
        } finally {
            target?.let {
                val bye = Protocol.bye(session)
                send(DatagramPacket(bye, bye.size, it))
            }
            runCatching { record.stop() }
            record.release()
        }
    }

    private fun streamLoop(address: InetSocketAddress, encoder: OpusEncoder) {
        val captureMs = AudioCapture.FRAME_MS +
            encoder.lookaheadSamples() * 1000 / AudioCapture.SAMPLE_RATE
        val sourceCode = if (source == AudioSourceType.INTERNAL) Protocol.SOURCE_INTERNAL else Protocol.SOURCE_MIC
        val frame = AudioCapture.FRAME_SAMPLES
        val pcm = ShortArray(frame * channels)
        var current = ByteArray(OpusEncoder.MAX_PACKET_BYTES)
        var previous = ByteArray(OpusEncoder.MAX_PACKET_BYTES)
        var previousLen = 0
        val out = ByteArray(Protocol.MAX_DATAGRAM)
        val datagram = DatagramPacket(out, out.size, address)
        var appliedKbps = StreamController.bitrateKbps
        var seq = 0L
        var nextHelloAt = 0L

        while (running && readFrame(pcm)) {
            val kbps = StreamController.bitrateKbps
            if (kbps != appliedKbps) {
                encoder.setBitrate(kbps * 1000)
                appliedKbps = kbps
            }
            val len = encoder.encode(pcm, frame, current)
            // Each datagram also carries the previous frame, so one lost packet costs nothing.
            val size = Protocol.writeAudio(out, session, seq, current, len, previous, previousLen)
            datagram.setData(out, 0, size)
            send(datagram)
            packetsSent.incrementAndGet()
            bytesSent.addAndGet(size.toLong())

            val swap = previous
            previous = current
            current = swap
            previousLen = len
            seq++

            val now = SystemClock.elapsedRealtime()
            if (now >= nextHelloAt) {
                val hello = Protocol.hello(
                    session, AudioCapture.SAMPLE_RATE, channels, AudioCapture.FRAME_MS,
                    appliedKbps, sourceCode, captureMs, deviceName,
                )
                send(DatagramPacket(hello, hello.size, address))
                nextHelloAt = now + HELLO_INTERVAL_MS
            }
        }
    }

    /** Blocking read of exactly one frame. False once [stop] was called. */
    private fun readFrame(pcm: ShortArray): Boolean {
        var offset = 0
        while (offset < pcm.size) {
            if (!running) return false
            val n = record.read(pcm, offset, pcm.size - offset)
            if (n < 0) throw IOException("AudioRecord.read failed ($n)")
            offset += n
        }
        return true
    }

    private fun send(packet: DatagramPacket) {
        try {
            socket.send(packet)
        } catch (e: IOException) {
            // Wi-Fi roaming or a brief disconnect: drop the packet, keep streaming.
            Log.w(TAG, "send failed: ${e.message}")
        }
    }

    private fun runControl() {
        val buf = ByteArray(1500)
        val packet = DatagramPacket(buf, buf.size)
        var lastReportAt = SystemClock.elapsedRealtime()
        var lastBytes = 0L
        while (running) {
            try {
                packet.setData(buf)
                socket.receive(packet)
                val ping = Protocol.parse(buf, packet.length) as? Protocol.Ping
                if (ping != null) {
                    val pong = Protocol.pong(ping.tNs)
                    send(DatagramPacket(pong, pong.size, packet.socketAddress))
                    lastPing = ping
                    lastPingAt = SystemClock.elapsedRealtime()
                }
            } catch (e: SocketTimeoutException) {
                // no PING this round
            } catch (e: IOException) {
                if (!running) break
                Log.w(TAG, "receive failed: ${e.message}")
            }

            val now = SystemClock.elapsedRealtime()
            if (now - lastReportAt >= STATS_INTERVAL_MS) {
                val bytes = bytesSent.get()
                val ping = lastPing
                onStats(
                    StreamStats(
                        packetsSent = packetsSent.get(),
                        kbps = ((bytes - lastBytes) * 8 / (now - lastReportAt)).toInt(),
                        receiverResponding = ping != null && now - lastPingAt < RECEIVER_TIMEOUT_MS,
                        receiverLossPercent = (ping?.lossPermille ?: 0) / 10f,
                        receiverJitterMs = (ping?.jitterUs ?: 0L) / 1000f,
                        receiverBufferMs = ping?.bufferMs ?: 0,
                    ),
                )
                lastBytes = bytes
                lastReportAt = now
            }
        }
    }

    private companion object {
        const val TAG = "UdpStreamer"
        const val STOP_TIMEOUT_MS = 1000L
        const val HELLO_INTERVAL_MS = 1000L
        const val STATS_INTERVAL_MS = 500L
        const val RECEIVER_TIMEOUT_MS = 2000L
    }
}
