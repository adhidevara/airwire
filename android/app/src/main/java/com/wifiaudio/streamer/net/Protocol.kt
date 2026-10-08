package com.wifiaudio.streamer.net

import java.nio.BufferUnderflowException
import java.nio.ByteBuffer

/** Wire format shared with the laptop receiver. Source of truth: PROTOCOL.md. */
object Protocol {
    const val DEFAULT_PORT = 50005
    const val VERSION = 1

    const val T_AUDIO = 0x01
    const val T_HELLO = 0x02
    const val T_DISCOVER = 0x10
    const val T_OFFER = 0x11
    const val T_PING = 0x20
    const val T_PONG = 0x21
    const val T_BYE = 0x30

    const val SOURCE_INTERNAL = 0
    const val SOURCE_MIC = 1

    const val FLAG_REDUNDANT = 0x01
    const val HEADER_BYTES = 4
    const val AUDIO_HEADER_BYTES = HEADER_BYTES + 9
    const val MAX_NAME_BYTES = 64

    /** Header + primary + redundant copy, each at most 1275 bytes. */
    const val MAX_DATAGRAM = AUDIO_HEADER_BYTES + 2 * 1275

    sealed interface Incoming
    data class Offer(val nonce: Int, val audioPort: Int, val name: String) : Incoming
    data class Ping(val tNs: Long, val lossPermille: Int, val jitterUs: Long, val bufferMs: Int) : Incoming

    private fun ByteBuffer.putHeader(type: Int): ByteBuffer = apply {
        put('W'.code.toByte())
        put('A'.code.toByte())
        put(VERSION.toByte())
        put(type.toByte())
    }

    /** Writes an AUDIO datagram into [out] without allocating; returns its length. */
    fun writeAudio(
        out: ByteArray,
        session: Int,
        seq: Long,
        primary: ByteArray,
        primaryLen: Int,
        redundant: ByteArray? = null,
        redundantLen: Int = 0,
    ): Int {
        val withRedundant = redundant != null && redundantLen > 0
        val buf = ByteBuffer.wrap(out).putHeader(T_AUDIO)
        buf.putShort(session.toShort())
        buf.putInt(seq.toInt())
        buf.put((if (withRedundant) FLAG_REDUNDANT else 0).toByte())
        buf.putShort(primaryLen.toShort())
        buf.put(primary, 0, primaryLen)
        if (withRedundant) buf.put(redundant, 0, redundantLen)
        return buf.position()
    }

    fun hello(
        session: Int,
        sampleRate: Int,
        channels: Int,
        frameMs: Int,
        bitrateKbps: Int,
        source: Int,
        captureMs: Int,
        name: String,
    ): ByteArray {
        val nameBytes = encodeName(name)
        return ByteBuffer.allocate(HEADER_BYTES + 13 + nameBytes.size)
            .putHeader(T_HELLO)
            .putShort(session.toShort())
            .putInt(sampleRate)
            .put(channels.toByte())
            .put(frameMs.toByte())
            .putShort(bitrateKbps.toShort())
            .put(source.toByte())
            .putShort(captureMs.coerceIn(0, 0xFFFF).toShort())
            .put(nameBytes)
            .array()
    }

    fun discover(nonce: Int): ByteArray =
        ByteBuffer.allocate(HEADER_BYTES + 4).putHeader(T_DISCOVER).putInt(nonce).array()

    fun pong(tNs: Long): ByteArray =
        ByteBuffer.allocate(HEADER_BYTES + 8).putHeader(T_PONG).putLong(tNs).array()

    fun bye(session: Int): ByteArray =
        ByteBuffer.allocate(HEADER_BYTES + 2).putHeader(T_BYE).putShort(session.toShort()).array()

    /** Parses the packets a sender receives (OFFER, PING); anything else is null. */
    fun parse(data: ByteArray, length: Int): Incoming? {
        if (length < HEADER_BYTES || length > data.size) return null
        if (data[0] != 'W'.code.toByte() || data[1] != 'A'.code.toByte()) return null
        if (data[2].toInt() != VERSION) return null
        val buf = ByteBuffer.wrap(data, HEADER_BYTES, length - HEADER_BYTES)
        return try {
            when (data[3].toInt() and 0xFF) {
                T_OFFER -> {
                    val nonce = buf.int
                    val port = buf.short.toInt() and 0xFFFF
                    val name = String(data, buf.position(), length - buf.position(), Charsets.UTF_8)
                    Offer(nonce, port, name)
                }
                T_PING -> Ping(
                    tNs = buf.long,
                    lossPermille = buf.short.toInt() and 0xFFFF,
                    jitterUs = buf.int.toLong() and 0xFFFFFFFFL,
                    bufferMs = buf.short.toInt() and 0xFFFF,
                )
                else -> null
            }
        } catch (e: BufferUnderflowException) {
            null
        }
    }

    /** UTF-8, at most [MAX_NAME_BYTES], never cutting a multi-byte character in half. */
    internal fun encodeName(name: String): ByteArray {
        val bytes = name.toByteArray(Charsets.UTF_8)
        if (bytes.size <= MAX_NAME_BYTES) return bytes
        var end = MAX_NAME_BYTES
        while (end > 0 && (bytes[end].toInt() and 0xC0) == 0x80) end--
        return bytes.copyOf(end)
    }
}
