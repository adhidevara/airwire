package com.wifiaudio.streamer.codec

/** What the jitter buffer needs from a decoder; lets unit tests run without JNI. */
interface FrameDecoder : AutoCloseable {
    val sampleRate: Int
    val channels: Int

    /**
     * Decodes into [out] (interleaved) and returns samples per channel. [packet] null runs
     * packet-loss concealment for [frameSize] samples; [fec] decodes the in-band FEC copy of
     * the previous frame carried in [packet].
     */
    fun decode(packet: ByteArray?, length: Int, out: ShortArray, frameSize: Int, fec: Boolean = false): Int

    fun reset()
}

/**
 * libopus decoder. Always decodes to [channels] channels, whatever the sender encoded.
 * Not thread-safe: use it from the playback thread only.
 */
class OpusDecoder(override val sampleRate: Int = 48_000, override val channels: Int = 2) : FrameDecoder {
    private var handle: Long = OpusNative.decoderCreate(sampleRate, channels)

    init {
        check(handle != 0L) { "opus_decoder_create failed ($sampleRate Hz, $channels ch)" }
    }

    override fun decode(packet: ByteArray?, length: Int, out: ShortArray, frameSize: Int, fec: Boolean): Int {
        check(handle != 0L) { "decoder is closed" }
        require(out.size >= frameSize * channels) { "out holds fewer than $frameSize frames" }
        val n = OpusNative.decode(handle, packet, length, out, frameSize, fec)
        if (n < 0) throw OpusException("opus_decode failed ($n)")
        return n
    }

    override fun reset() {
        if (handle != 0L) OpusNative.decoderReset(handle)
    }

    override fun close() {
        if (handle != 0L) {
            OpusNative.decoderDestroy(handle)
            handle = 0L
        }
    }

    companion object {
        /** Longest Opus frame (120 ms at 48 kHz), the most one packet can decode to. */
        const val MAX_FRAME_SAMPLES = 5760
    }
}

class OpusException(message: String) : RuntimeException(message)
