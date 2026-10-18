"""Wire format shared with the Android sender. Source of truth: PROTOCOL.md."""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"WA"
VERSION = 1
DEFAULT_PORT = 50005
MAX_NAME_BYTES = 64

T_AUDIO = 0x01
T_HELLO = 0x02
T_DISCOVER = 0x10
T_OFFER = 0x11
T_PING = 0x20
T_PONG = 0x21
T_BYE = 0x30

SOURCE_INTERNAL = 0
SOURCE_MIC = 1

FLAG_REDUNDANT = 0x01

_HEADER = struct.Struct(">2sBB")
_AUDIO = struct.Struct(">HIBH")
_HELLO = struct.Struct(">HIBBHBH")
_DISCOVER = struct.Struct(">I")
_OFFER = struct.Struct(">IH")
_PING = struct.Struct(">QHIH")
_PONG = struct.Struct(">Q")
_BYE = struct.Struct(">H")


@dataclass(frozen=True)
class Audio:
    session: int
    seq: int
    primary: bytes
    redundant: bytes | None = None


@dataclass(frozen=True)
class Hello:
    session: int
    sample_rate: int
    channels: int
    frame_ms: int
    bitrate_kbps: int
    source: int
    capture_ms: int
    name: str


@dataclass(frozen=True)
class Discover:
    nonce: int


@dataclass(frozen=True)
class Offer:
    nonce: int
    audio_port: int
    name: str


@dataclass(frozen=True)
class Ping:
    t_ns: int
    loss_permille: int
    jitter_us: int
    buffer_ms: int


@dataclass(frozen=True)
class Pong:
    t_ns: int


@dataclass(frozen=True)
class Bye:
    session: int


Packet = Audio | Hello | Discover | Offer | Ping | Pong | Bye


def _header(ptype: int) -> bytes:
    return _HEADER.pack(MAGIC, VERSION, ptype)


def _encode_name(name: str) -> bytes:
    raw = name.encode("utf-8")[:MAX_NAME_BYTES]
    # Don't leave a split multi-byte character at the end.
    return raw.decode("utf-8", "ignore").encode("utf-8")


def _clamp(value: int, hi: int) -> int:
    return max(0, min(int(value), hi))


def encode(packet: Packet) -> bytes:
    match packet:
        case Audio(session, seq, primary, redundant):
            flags = FLAG_REDUNDANT if redundant else 0
            body = _AUDIO.pack(session, seq & 0xFFFFFFFF, flags, len(primary)) + primary
            return _header(T_AUDIO) + body + (redundant or b"")
        case Hello():
            p = packet
            body = _HELLO.pack(
                p.session, p.sample_rate, p.channels, p.frame_ms,
                p.bitrate_kbps, p.source, _clamp(p.capture_ms, 0xFFFF),
            )
            return _header(T_HELLO) + body + _encode_name(p.name)
        case Discover(nonce):
            return _header(T_DISCOVER) + _DISCOVER.pack(nonce)
        case Offer(nonce, port, name):
            return _header(T_OFFER) + _OFFER.pack(nonce, port) + _encode_name(name)
        case Ping(t_ns, loss, jitter, buffer_ms):
            body = _PING.pack(
                t_ns, _clamp(loss, 1000), _clamp(jitter, 0xFFFFFFFF), _clamp(buffer_ms, 0xFFFF)
            )
            return _header(T_PING) + body
        case Pong(t_ns):
            return _header(T_PONG) + _PONG.pack(t_ns)
        case Bye(session):
            return _header(T_BYE) + _BYE.pack(session)
    raise TypeError(f"not a packet: {packet!r}")


def decode(data: bytes) -> Packet | None:
    """Parse one datagram. Returns None for anything malformed or foreign."""
    if len(data) < _HEADER.size:
        return None
    magic, version, ptype = _HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION:
        return None
    body = memoryview(data)[_HEADER.size:]
    try:
        if ptype == T_AUDIO:
            session, seq, flags, plen = _AUDIO.unpack_from(body)
            start = _AUDIO.size
            if plen == 0 or start + plen > len(body):
                return None
            primary = bytes(body[start:start + plen])
            rest = bytes(body[start + plen:])
            redundant = rest if (flags & FLAG_REDUNDANT) and rest else None
            return Audio(session, seq, primary, redundant)
        if ptype == T_HELLO:
            fields = _HELLO.unpack_from(body)
            name = bytes(body[_HELLO.size:]).decode("utf-8", "replace")
            return Hello(*fields, name=name)
        if ptype == T_DISCOVER:
            return Discover(*_DISCOVER.unpack_from(body))
        if ptype == T_OFFER:
            nonce, port = _OFFER.unpack_from(body)
            return Offer(nonce, port, bytes(body[_OFFER.size:]).decode("utf-8", "replace"))
        if ptype == T_PING:
            return Ping(*_PING.unpack_from(body))
        if ptype == T_PONG:
            return Pong(*_PONG.unpack_from(body))
        if ptype == T_BYE:
            return Bye(*_BYE.unpack_from(body))
    except struct.error:
        return None
    return None
