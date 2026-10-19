package com.wifiaudio.streamer.net

import java.nio.BufferUnderflowException
import java.nio.ByteBuffer
import java.security.MessageDigest
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

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
    const val T_LISTEN = 0x40
    const val T_REFUSE = 0x41

    const val SOURCE_INTERNAL = 0
    const val SOURCE_MIC = 1
    const val SOURCE_PC = 2

    // REFUSE reasons
    const val REFUSE_NOT_PAIRED = 1
    const val REFUSE_DISABLED = 2
    const val REFUSE_NO_CAPTURE = 3
    const val REFUSE_STOPPED = 4

    const val LISTEN_MAC_BYTES = 16

    const val FLAG_REDUNDANT = 0x01
    const val HEADER_BYTES = 4
    const val AUDIO_HEADER_BYTES = HEADER_BYTES + 9
    const val MAX_NAME_BYTES = 64

    /** Header + primary + redundant copy, each at most 1275 bytes. */
    const val MAX_DATAGRAM = AUDIO_HEADER_BYTES + 2 * 1275

    sealed interface Incoming
    data class Offer(val nonce: Int, val audioPort: Int, val name: String) : Incoming
    data class Ping(val tNs: Long, val lossPermille: Int, val jitterUs: Long, val bufferMs: Int) : Incoming
    data class Pong(val tNs: Long) : Incoming
    data class Refuse(val session: Int, val reason: Int) : Incoming

    /** [primary] and [redundant] are copies; [seq] is unsigned 32-bit. */
    class Audio(val session: Int, val seq: Long, val primary: ByteArray, val redundant: ByteArray?) : Incoming

    data class Hello(
        val session: Int,
        val sampleRate: Int,
        val channels: Int,
        val frameMs: Int,
        val bitrateKbps: Int,
        val source: Int,
        val captureMs: Int,
        val name: String,
    ) : Incoming

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

    fun ping(tNs: Long, lossPermille: Int, jitterUs: Long, bufferMs: Int): ByteArray =
        ByteBuffer.allocate(HEADER_BYTES + 16).putHeader(T_PING)
            .putLong(tNs)
            .putShort(lossPermille.coerceIn(0, 1000).toShort())
            .putInt(jitterUs.coerceIn(0, 0xFFFFFFFFL).toInt())
            .putShort(bufferMs.coerceIn(0, 0xFFFF).toShort())
            .array()

    /** Key for [listen]: SHA-256 of the file-transfer pairing token, which the PC also keeps. */
    fun pairingKey(token: String): ByteArray =
        MessageDigest.getInstance("SHA-256").digest(token.toByteArray(Charsets.UTF_8))

    /** HMAC-SHA256 over the header and fixed LISTEN fields, truncated to 16 bytes. */
    fun listenMac(key: ByteArray, session: Int, bitrateKbps: Int, counter: Long): ByteArray {
        val fields = ByteBuffer.allocate(HEADER_BYTES + 12).putHeader(T_LISTEN)
            .putShort(session.toShort())
            .putShort(bitrateKbps.toShort())
            .putLong(counter)
            .array()
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(key, "HmacSHA256")) }
        return mac.doFinal(fields).copyOf(LISTEN_MAC_BYTES)
    }

    /** Asks the PC to stream its audio here; [counter] must grow with every packet. */
    fun listen(key: ByteArray, session: Int, bitrateKbps: Int, counter: Long, name: String): ByteArray {
        val nameBytes = encodeName(name)
        return ByteBuffer.allocate(HEADER_BYTES + 12 + LISTEN_MAC_BYTES + nameBytes.size)
            .putHeader(T_LISTEN)
            .putShort(session.toShort())
            .putShort(bitrateKbps.toShort())
            .putLong(counter)
            .put(listenMac(key, session, bitrateKbps, counter))
            .put(nameBytes)
            .array()
    }

    /** Parses OFFER and PING (sending) and AUDIO, HELLO, PONG, REFUSE (listening). */
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
                T_PONG -> Pong(buf.long)
                T_REFUSE -> Refuse(buf.short.toInt() and 0xFFFF, buf.get().toInt() and 0xFF)
                T_AUDIO -> {
                    val session = buf.short.toInt() and 0xFFFF
                    val seq = buf.int.toLong() and 0xFFFFFFFFL
                    val flags = buf.get().toInt()
                    val primaryLen = buf.short.toInt() and 0xFFFF
                    val start = buf.position()
                    if (primaryLen == 0 || start + primaryLen > length) return null
                    val primary = data.copyOfRange(start, start + primaryLen)
                    val rest = start + primaryLen
                    val redundant = if (flags and FLAG_REDUNDANT != 0 && rest < length) {
                        data.copyOfRange(rest, length)
                    } else {
                        null
                    }
                    Audio(session, seq, primary, redundant)
                }
                T_HELLO -> Hello(
                    session = buf.short.toInt() and 0xFFFF,
                    sampleRate = buf.int,
                    channels = buf.get().toInt() and 0xFF,
                    frameMs = buf.get().toInt() and 0xFF,
                    bitrateKbps = buf.short.toInt() and 0xFFFF,
                    source = buf.get().toInt() and 0xFF,
                    captureMs = buf.short.toInt() and 0xFFFF,
                    name = String(data, buf.position(), length - buf.position(), Charsets.UTF_8),
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
