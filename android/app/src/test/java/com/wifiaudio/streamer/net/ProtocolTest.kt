package com.wifiaudio.streamer.net

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Byte vectors are produced by the Python receiver (receiver/tests/test_protocol.py). */
class ProtocolTest {

    private fun ByteArray.hex() = joinToString("") { "%02x".format(it) }
    private fun String.unhex() = chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    @Test
    fun audioWithRedundantCopy() {
        val out = ByteArray(Protocol.MAX_DATAGRAM)
        val n = Protocol.writeAudio(
            out, session = 0x1234, seq = 5,
            primary = byteArrayOf(0xAA.toByte()), primaryLen = 1,
            redundant = byteArrayOf(0xBB.toByte()), redundantLen = 1,
        )
        assertEquals("57410101123400000005010001aabb", out.copyOf(n).hex())
    }

    @Test
    fun audioWithoutRedundantCopy() {
        val out = ByteArray(Protocol.MAX_DATAGRAM)
        val n = Protocol.writeAudio(out, 1, 9, byteArrayOf(0xAA.toByte(), 0x01, 0x02), 1)
        assertEquals("57410101000100000009000001aa", out.copyOf(n).hex())
    }

    @Test
    fun hello() {
        val bytes = Protocol.hello(0x0102, 48000, 2, 10, 128, Protocol.SOURCE_MIC, 25, "A")
        assertEquals("5741010201020000bb80020a008001001941", bytes.hex())
    }

    @Test
    fun smallPackets() {
        assertEquals("5741011001020304", Protocol.discover(0x01020304).hex())
        assertEquals("574101210000001cbe991a14", Protocol.pong(123456789012).hex())
        assertEquals("574101300102", Protocol.bye(0x0102).hex())
    }

    @Test
    fun parsesOffer() {
        val data = "574101110000002ac3554c4150544f50".unhex()
        assertEquals(Protocol.Offer(42, 50005, "LAPTOP"), Protocol.parse(data, data.size))
    }

    @Test
    fun parsesPing() {
        val data = "574101200000001cbe991a14000f000008fc0014".unhex()
        assertEquals(
            Protocol.Ping(tNs = 123456789012, lossPermille = 15, jitterUs = 2300, bufferMs = 20),
            Protocol.parse(data, data.size),
        )
    }

    @Test
    fun listenMatchesPython() {
        val key = Protocol.pairingKey("test-token")
        assertEquals("aa1b3c7b2972fc3e1494eb827bae358e", Protocol.listenMac(key, 0x1234, 96, 1_700_000_000_000).hex())
        assertEquals(
            "57410140123400600000018bcfe56800aa1b3c7b2972fc3e1494eb827bae358e506978656c",
            Protocol.listen(key, 0x1234, 96, 1_700_000_000_000, "Pixel").hex(),
        )
    }

    @Test
    fun pingMatchesPython() {
        assertEquals("574101200000001cbe991a14000f000008fc0014", Protocol.ping(123456789012, 15, 2300, 20).hex())
    }

    @Test
    fun parsesAudio() {
        val data = "574101011234fffffff0010002aabbcc".unhex()
        val a = Protocol.parse(data, data.size) as Protocol.Audio
        assertEquals(0x1234, a.session)
        assertEquals(0xFFFFFFF0L, a.seq)
        assertEquals("aabb", a.primary.hex())
        assertEquals("cc", a.redundant!!.hex())
    }

    @Test
    fun parsesHelloPongRefuse() {
        val hello = "5741010200070000bb80020a00800200054d792d5043".unhex()
        assertEquals(
            Protocol.Hello(7, 48000, 2, 10, 128, Protocol.SOURCE_PC, 5, "My-PC"),
            Protocol.parse(hello, hello.size),
        )
        val pong = "574101210000001cbe991a14".unhex()
        assertEquals(Protocol.Pong(123456789012), Protocol.parse(pong, pong.size))
        val refuse = "57410141000701".unhex()
        assertEquals(Protocol.Refuse(7, Protocol.REFUSE_NOT_PAIRED), Protocol.parse(refuse, refuse.size))
    }

    @Test
    fun rejectsMalformed() {
        val ping = "574101200000001cbe991a14000f000008fc0014".unhex()
        assertNull(Protocol.parse(ping, 10)) // truncated
        assertNull(Protocol.parse("584101200000".unhex(), 6)) // bad magic
        assertNull(Protocol.parse("574102110000002a".unhex(), 8)) // bad version
        assertNull(Protocol.parse("5741017f".unhex(), 4)) // unknown type
    }

    @Test
    fun namesAreTruncatedOnCharacterBoundary() {
        val name = "é".repeat(40) // 80 bytes
        assertEquals("é".repeat(32), String(Protocol.encodeName(name), Charsets.UTF_8))
        assertEquals("short", String(Protocol.encodeName("short"), Charsets.UTF_8))
    }
}
