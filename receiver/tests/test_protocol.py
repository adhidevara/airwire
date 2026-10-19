import hashlib

import pytest

from airwire import protocol as p

ROUNDTRIP = [
    p.Audio(session=0xBEEF, seq=123456, primary=b"\x01\x02\x03", redundant=b"\x04\x05"),
    p.Audio(session=1, seq=0, primary=b"\xff"),
    p.Hello(session=7, sample_rate=48000, channels=2, frame_ms=10, bitrate_kbps=128,
            source=p.SOURCE_INTERNAL, capture_ms=20, name="Pixel 8"),
    p.Discover(nonce=0xDEADBEEF),
    p.Offer(nonce=42, audio_port=50005, name="LAPTOP-01"),
    p.Ping(t_ns=2**63 - 1, loss_permille=15, jitter_us=2300, buffer_ms=20),
    p.Pong(t_ns=987654321),
    p.Bye(session=7),
    p.Listen(session=0x1234, bitrate_kbps=96, counter=1_700_000_000_000, mac=bytes(range(16)),
             name="Pixel"),
    p.Refuse(session=7, reason=p.REFUSE_NOT_PAIRED),
]


@pytest.mark.parametrize("packet", ROUNDTRIP, ids=lambda x: type(x).__name__)
def test_roundtrip(packet):
    assert p.decode(p.encode(packet)) == packet


def test_audio_golden_bytes():
    # Same vector is asserted in the Android ProtocolTest; keep both in sync.
    data = p.encode(p.Audio(session=0x1234, seq=5, primary=b"\xaa", redundant=b"\xbb"))
    assert data == bytes.fromhex("5741 0101 1234 00000005 01 0001 aa bb".replace(" ", ""))


def test_hello_golden_bytes():
    data = p.encode(p.Hello(session=0x0102, sample_rate=48000, channels=2, frame_ms=10,
                            bitrate_kbps=128, source=1, capture_ms=25, name="A"))
    assert data == bytes.fromhex("5741 0102 0102 0000bb80 02 0a 0080 01 0019 41".replace(" ", ""))


@pytest.mark.parametrize("data", [
    b"",
    b"WA\x01",
    b"XX\x01\x01" + b"\x00" * 12,          # wrong magic
    b"WA\x02\x01" + b"\x00" * 12,          # wrong version
    b"WA\x01\x7f",                         # unknown type
    b"WA\x01\x01\x00\x01\x00\x00\x00\x01\x00\x00\x10\xaa",  # primaryLen past the end
    b"WA\x01\x01\x00\x01\x00\x00\x00\x01\x00\x00\x00",      # empty primary
    b"WA\x01\x20\x00\x00",                 # truncated ping
])
def test_rejects_malformed(data):
    assert p.decode(data) is None


def test_redundant_flag_without_payload_is_ignored():
    data = b"WA\x01\x01\x00\x01\x00\x00\x00\x09\x01\x00\x01\xaa"
    assert p.decode(data) == p.Audio(1, 9, b"\xaa", None)


def test_name_is_truncated_on_a_character_boundary():
    name = "é" * 40  # 80 bytes in UTF-8
    decoded = p.decode(p.encode(p.Offer(1, 50005, name)))
    assert decoded.name == "é" * 32


def test_ping_values_are_clamped():
    decoded = p.decode(p.encode(p.Ping(1, loss_permille=5000, jitter_us=-3, buffer_ms=10**6)))
    assert (decoded.loss_permille, decoded.jitter_us, decoded.buffer_ms) == (1000, 0, 0xFFFF)


def test_listen_golden_bytes():
    # Same vector as ProtocolTest.kt: key = SHA-256("test-token").
    key = hashlib.sha256(b"test-token").digest()
    mac = p.listen_mac(key, 0x1234, 96, 1_700_000_000_000)
    assert mac.hex() == "aa1b3c7b2972fc3e1494eb827bae358e"
    data = p.encode(p.Listen(0x1234, 96, 1_700_000_000_000, mac, "Pixel"))
    assert data == bytes.fromhex(
        "5741 0140 1234 0060 0000018bcfe56800 aa1b3c7b2972fc3e1494eb827bae358e 506978656c"
        .replace(" ", ""))
    assert p.encode(p.Refuse(7, p.REFUSE_NOT_PAIRED)) == bytes.fromhex("57410141000701")
