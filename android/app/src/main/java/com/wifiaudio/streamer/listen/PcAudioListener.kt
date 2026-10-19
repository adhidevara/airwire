package com.wifiaudio.streamer.listen

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Process
import android.os.SystemClock
import android.util.Log
import com.wifiaudio.streamer.codec.OpusDecoder
import com.wifiaudio.streamer.net.Protocol
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.SocketTimeoutException
import kotlin.concurrent.thread
import kotlin.math.roundToInt
import kotlin.random.Random

/**
 * PC -> phone: asks the PC for its audio (LISTEN every second, authenticated with the file
 * pairing token), buffers what arrives and plays it through a low-latency AudioTrack.
 *
 * The network thread owns the socket and the stats; the playback thread owns the AudioTrack
 * and pulls one frame at a time from the jitter buffer, which decodes on that thread.
 */
class PcAudioListener(
    private val host: String,
    private val port: Int,
    token: String,
    private val deviceName: String,
    private val onStatus: (ListenStatus) -> Unit,
    private val onStats: (ListenStats) -> Unit,
    private val onFatal: (String) -> Unit,
) {
    private val key = Protocol.pairingKey(token)
    private val session = Random.nextInt(1, 0x10000)
    private val target = "$host:$port"
    private val socket = DatagramSocket().apply {
        soTimeout = 100
        receiveBufferSize = 1 shl 20
        trafficClass = TOS_EXPEDITED // asks Wi-Fi (WMM) for the voice/video queue
    }
    private val decoder = OpusDecoder(SAMPLE_RATE, CHANNELS)
    private val jitter = JitterBuffer(decoder)
    private val stats = NetStats()
    private val statsLock = Any()

    @Volatile private var running = false
    @Volatile private var volume = 1f
    @Volatile private var track: AudioTrack? = null
    @Volatile private var outputMs = 0.0
    private var netThread: Thread? = null
    private var playThread: Thread? = null

    fun start() {
        running = true
        netThread = thread(name = "airwire-listen-net") { runNetwork() }
        playThread = thread(name = "airwire-listen-play") { runPlayback() }
    }

    /** Blocks briefly while both threads finish; sends BYE. */
    fun stop() {
        running = false
        netThread?.join(STOP_TIMEOUT_MS)
        playThread?.join(STOP_TIMEOUT_MS)
        socket.close()
        decoder.close()
    }

    /** 0..1, for audio-focus ducking; the phone's media volume still applies on top. */
    fun setVolume(value: Float) {
        volume = value
        track?.setVolume(value)
    }

    // ---- network ------------------------------------------------------------------------

    private fun runNetwork() {
        var address: InetSocketAddress? = null
        try {
            val pc = InetSocketAddress(InetAddress.getByName(host), port)
            address = pc
            receiveLoop(pc)
        } catch (e: Exception) {
            Log.e(TAG, "listening stopped", e)
            if (running) fail(e.message ?: e.javaClass.simpleName)
        } finally {
            address?.let { send(Protocol.bye(session), it) }
        }
    }

    private fun receiveLoop(pc: InetSocketAddress) {
        val buf = ByteArray(4096)
        val packet = DatagramPacket(buf, buf.size)
        var streamSession = -1
        var pcName: String? = null
        var captureMs = 0
        var counter = 0L
        var lastAudioAt = 0L
        var nextListen = 0L
        var nextPing = 0L
        var nextReport = 0L

        while (running) {
            val nowMs = SystemClock.elapsedRealtime()
            if (nowMs >= nextListen) {
                counter = maxOf(System.currentTimeMillis(), counter + 1)
                send(Protocol.listen(key, session, ListenController.bitrateKbps, counter, deviceName), pc)
                nextListen = nowMs + LISTEN_INTERVAL_MS
            }
            if (nowMs >= nextPing) {
                val ping = synchronized(statsLock) {
                    stats.sample(nowMs / 1000.0)
                    Protocol.ping(
                        System.nanoTime(), (stats.loss * 1000).roundToInt(),
                        (stats.jitterMs * 1000).roundToInt().toLong(), jitter.bufferMs.roundToInt(),
                    )
                }
                send(ping, pc)
                nextPing = nowMs + PING_INTERVAL_MS
            }
            if (nowMs >= nextReport) {
                onStats(snapshot(receiving = nowMs - lastAudioAt < RECEIVING_TIMEOUT_MS, captureMs))
                nextReport = nowMs + PING_INTERVAL_MS
            }

            try {
                packet.setData(buf)
                socket.receive(packet)
            } catch (e: SocketTimeoutException) {
                continue
            } catch (e: IOException) {
                if (!running) break
                Log.w(TAG, "receive failed: ${e.message}")
                Thread.sleep(50)
                continue
            }
            if (packet.address != pc.address) continue // only the PC we asked
            val now = System.nanoTime() / 1e9
            when (val p = Protocol.parse(buf, packet.length)) {
                is Protocol.Audio -> {
                    if (p.session != streamSession) {
                        // Same PC, same Wi-Fi: keep what the buffer learned about it.
                        jitter.reset(keepHistory = streamSession != -1)
                        streamSession = p.session
                        synchronized(statsLock) { stats.reset() }
                    }
                    jitter.push(p.seq, p.primary, p.redundant)
                    synchronized(statsLock) { stats.onAudio(p.seq, packet.length, now) }
                    lastAudioAt = SystemClock.elapsedRealtime()
                }
                is Protocol.Hello -> {
                    captureMs = p.captureMs
                    if (p.name != pcName) {
                        pcName = p.name
                        onStatus(ListenStatus.Playing(target, p.name))
                    }
                }
                is Protocol.Pong -> synchronized(statsLock) { stats.onPong(p.tNs, System.nanoTime()) }
                is Protocol.Refuse -> if (p.session == session) {
                    fail(refuseMessage(p.reason))
                    return
                }
                else -> Unit
            }
        }
    }

    private fun snapshot(receiving: Boolean, captureMs: Int): ListenStats = synchronized(statsLock) {
        val js = jitter.stats()
        val rtt = stats.rttMs
        ListenStats(
            packets = stats.received,
            kbps = stats.kbps.roundToInt(),
            lossPercent = (stats.loss * 100).toFloat(),
            jitterMs = stats.jitterMs.toFloat(),
            bufferMs = jitter.bufferMs.roundToInt(),
            rttMs = rtt?.toFloat(),
            latencyMs = if (receiving && jitter.playing) {
                (captureMs + jitter.frameMs + (rtt ?: 0.0) / 2 + jitter.waitMs + outputMs).roundToInt()
            } else {
                null
            },
            concealed = js.concealed + js.fec,
            receiving = receiving,
        )
    }

    private fun send(data: ByteArray, to: InetSocketAddress) {
        try {
            socket.send(DatagramPacket(data, data.size, to))
        } catch (e: IOException) {
            Log.w(TAG, "send failed: ${e.message}")
        }
    }

    private fun fail(message: String) {
        running = false
        onFatal(message)
    }

    // ---- playback -----------------------------------------------------------------------

    private fun runPlayback() {
        Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        val frameBytes = FRAME_SAMPLES * CHANNELS * 2
        val minBuffer = AudioTrack.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_OUT_STEREO, AudioFormat.ENCODING_PCM_16BIT)
        val t = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                    // Never re-captured by "phone -> PC" streaming, so no feedback loop.
                    .setAllowedCapturePolicy(AudioAttributes.ALLOW_CAPTURE_BY_NONE)
                    .build(),
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO)
                    .build(),
            )
            .setTransferMode(AudioTrack.MODE_STREAM)
            .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
            .setBufferSizeInBytes(maxOf(minBuffer, 2 * frameBytes))
            .build()
        track = t
        t.setVolume(volume)
        outputMs = t.bufferSizeInFrames * 1000.0 / SAMPLE_RATE
        val out = ShortArray(JitterBuffer.MAX_FRAME_SAMPLES * CHANNELS)
        val silence = ShortArray(FRAME_SAMPLES * CHANNELS)
        try {
            t.play()
            while (running) {
                val n = jitter.pop(out)
                val written = if (n > 0) {
                    t.write(out, 0, n * CHANNELS, AudioTrack.WRITE_BLOCKING)
                } else {
                    t.write(silence, 0, silence.size, AudioTrack.WRITE_BLOCKING)
                }
                check(written >= 0) { "AudioTrack.write failed ($written)" }
            }
        } catch (e: Exception) {
            Log.e(TAG, "playback stopped", e)
            if (running) fail("Playback failed: ${e.message}")
        } finally {
            track = null
            runCatching { t.stop() }
            t.release()
        }
    }

    companion object {
        private const val TAG = "PcAudioListener"
        const val SAMPLE_RATE = 48_000
        const val CHANNELS = 2
        private const val FRAME_SAMPLES = SAMPLE_RATE / 100
        private const val LISTEN_INTERVAL_MS = 1000L
        private const val PING_INTERVAL_MS = 500L
        private const val RECEIVING_TIMEOUT_MS = 1500L
        private const val STOP_TIMEOUT_MS = 1000L
        /** DSCP EF (46) << 2. */
        const val TOS_EXPEDITED = 0xB8

        fun refuseMessage(reason: Int): String = when (reason) {
            Protocol.REFUSE_NOT_PAIRED -> "The PC doesn't recognise this phone. Pair again in the Files tab."
            Protocol.REFUSE_DISABLED -> "Playing the PC's audio is turned off in the Airwire window on the PC."
            Protocol.REFUSE_NO_CAPTURE -> "The PC can't capture its audio. See the Airwire window on the PC."
            Protocol.REFUSE_STOPPED -> "Stopped from the PC."
            else -> "The PC refused (code $reason)."
        }
    }
}
