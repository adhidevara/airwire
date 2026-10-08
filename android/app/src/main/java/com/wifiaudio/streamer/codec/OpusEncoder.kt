package com.wifiaudio.streamer.codec

/** JNI entry points implemented in src/main/cpp/opus_jni.c. */
object OpusNative {
    init {
        System.loadLibrary("opusjni")
    }

    @JvmStatic
    external fun create(
        sampleRate: Int,
        channels: Int,
        application: Int,
        bitrate: Int,
        complexity: Int,
        inbandFec: Boolean,
        packetLossPerc: Int,
    ): Long

    @JvmStatic
    external fun encode(handle: Long, pcm: ShortArray, frameSize: Int, out: ByteArray): Int

    @JvmStatic
    external fun setBitrate(handle: Long, bitrate: Int): Int

    @JvmStatic
    external fun getLookahead(handle: Long): Int

    @JvmStatic
    external fun destroy(handle: Long)
}

/**
 * libopus encoder. Not thread-safe: create, use and close it on the streaming thread.
 */
class OpusEncoder(
    sampleRate: Int,
    val channels: Int,
    bitrate: Int,
    complexity: Int = 8,
    application: Int = APPLICATION_AUDIO,
    inbandFec: Boolean = true,
    packetLossPerc: Int = 10,
) : AutoCloseable {

    private var handle: Long = OpusNative.create(
        sampleRate, channels, application, bitrate, complexity, inbandFec, packetLossPerc,
    )

    init {
        check(handle != 0L) { "opus_encoder_create failed ($sampleRate Hz, $channels ch)" }
    }

    /** Encodes [frameSize] samples per channel from interleaved [pcm]; returns bytes written. */
    fun encode(pcm: ShortArray, frameSize: Int, out: ByteArray): Int {
        check(handle != 0L) { "encoder is closed" }
        require(pcm.size >= frameSize * channels) { "pcm holds fewer than $frameSize frames" }
        val n = OpusNative.encode(handle, pcm, frameSize, out)
        check(n >= 0) { "opus_encode failed ($n)" }
        return n
    }

    fun setBitrate(bitrate: Int) {
        OpusNative.setBitrate(handle, bitrate)
    }

    /** Encoder algorithmic delay in samples (≈6.5 ms for APPLICATION_AUDIO). */
    fun lookaheadSamples(): Int = OpusNative.getLookahead(handle)

    override fun close() {
        if (handle != 0L) {
            OpusNative.destroy(handle)
            handle = 0L
        }
    }

    companion object {
        const val APPLICATION_VOIP = 2048
        const val APPLICATION_AUDIO = 2049
        const val APPLICATION_RESTRICTED_LOWDELAY = 2051

        /** Largest packet libopus produces for one frame. */
        const val MAX_PACKET_BYTES = 1275
    }
}
