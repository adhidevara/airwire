"""PC -> phone: stream what this PC is playing to paired phones that ask for it.

A phone sends LISTEN every second while it wants audio. LISTEN carries an HMAC under
SHA-256(pairing token), the same hash the PC keeps for file transfer, plus a counter that
must increase, so only paired phones can start a stream and captured packets can't be
replayed. Each listener gets its own Opus encoder and session; the system audio is
captured once and sent to every listener. Phones report their stats with PING; the PC
answers PONG so they can measure the round trip.
"""

from __future__ import annotations

import dataclasses
import hmac
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import protocol as proto
from .capture import CaptureError, LoopbackCapture
from .files import Pairing
from .opus import OpusEncoder

log = logging.getLogger(__name__)

Addr = tuple[str, int]
SendFn = Callable[[proto.Packet, Addr], None]


@dataclass
class ListenerInfo:
    addr: Addr
    device: str
    session: int
    bitrate_kbps: int
    since: float
    last_seen: float
    loss_permille: int = 0
    jitter_us: int = 0
    buffer_ms: int = 0


@dataclass
class _Listener:
    info: ListenerInfo
    encoder: OpusEncoder
    stream_session: int
    applied_kbps: int = 0
    seq: int = 0
    prev: bytes | None = None
    next_hello: float = 0.0


class PcAudioBroadcaster:
    TIMEOUT_S = 3.0
    HELLO_INTERVAL_S = 1.0
    MIN_KBPS, MAX_KBPS = 32, 256

    def __init__(self, pairing: Pairing | None, send: SendFn, *, name: str,
                 enabled: bool = True,
                 capture_factory: Callable[..., LoopbackCapture] = LoopbackCapture,
                 clock: Callable[[], float] = time.monotonic):
        self.pairing = pairing
        self.enabled = enabled
        self.name = name
        self.capture_error: str | None = None
        self.on_change: Callable[[], None] | None = None
        self._send = send
        self._capture_factory = capture_factory
        self._clock = clock
        self._lock = threading.Lock()
        self._listeners: dict[Addr, _Listener] = {}
        self._counters: dict[str, int] = {}        # pairing key hash -> last LISTEN counter
        self._stopped: set[tuple[Addr, int]] = set()
        self._capture: LoopbackCapture | None = None

    # ---- packets from the UDP thread ------------------------------------------

    def handle_listen(self, pkt: proto.Listen, addr: Addr) -> None:
        now = self._clock()
        if not self.enabled:
            self._send(proto.Refuse(pkt.session, proto.REFUSE_DISABLED), addr)
            return
        device = self._authenticate(pkt)
        if device is None:
            self._send(proto.Refuse(pkt.session, proto.REFUSE_NOT_PAIRED), addr)
            return
        if (addr, pkt.session) in self._stopped:
            self._send(proto.Refuse(pkt.session, proto.REFUSE_STOPPED), addr)
            return
        kbps = max(self.MIN_KBPS, min(self.MAX_KBPS, pkt.bitrate_kbps))
        changed = False
        with self._lock:
            current = self._listeners.get(addr)
            if current is None or current.info.session != pkt.session:
                log.info("%s (%s:%d) started listening at %d kbps", device, *addr, kbps)
                self._listeners[addr] = _Listener(
                    ListenerInfo(addr, device, pkt.session, kbps, since=now, last_seen=now),
                    OpusEncoder(channels=2, bitrate=kbps * 1000),
                    stream_session=random.randrange(1, 1 << 16), applied_kbps=kbps)
                changed = True
            else:
                current.info.last_seen = now
                current.info.bitrate_kbps = kbps  # applied by the capture thread
        if not self._ensure_capture():
            with self._lock:
                self._listeners.pop(addr, None)
            self._send(proto.Refuse(pkt.session, proto.REFUSE_NO_CAPTURE), addr)
            changed = True
        if changed:
            self._changed()

    def handle_ping(self, pkt: proto.Ping, addr: Addr) -> bool:
        """Stats from a listening phone. False if `addr` isn't listening."""
        with self._lock:
            listener = self._listeners.get(addr)
            if listener is None:
                return False
            info = listener.info
            info.loss_permille, info.jitter_us, info.buffer_ms = (
                pkt.loss_permille, pkt.jitter_us, pkt.buffer_ms)
        self._send(proto.Pong(pkt.t_ns), addr)
        return True

    def handle_bye(self, pkt: proto.Bye, addr: Addr) -> bool:
        with self._lock:
            listener = self._listeners.get(addr)
            if listener is None or listener.info.session != pkt.session:
                return False
            del self._listeners[addr]
        log.info("%s stopped listening", listener.info.device)
        self._after_removal()
        return True

    def tick(self) -> None:
        """Drop phones that stopped sending LISTEN."""
        now = self._clock()
        with self._lock:
            gone = [a for a, l in self._listeners.items() if now - l.info.last_seen > self.TIMEOUT_S]
            for addr in gone:
                log.info("%s timed out", self._listeners.pop(addr).info.device)
        if gone:
            self._after_removal()

    # ---- control ---------------------------------------------------------------

    def listeners(self) -> list[ListenerInfo]:
        with self._lock:
            return [dataclasses.replace(l.info) for l in self._listeners.values()]

    def stop_all(self) -> None:
        """Stop streaming to every phone; they get REFUSE_STOPPED until they start again."""
        with self._lock:
            for addr, listener in self._listeners.items():
                self._stopped.add((addr, listener.info.session))
            self._listeners.clear()
        self._after_removal()

    def close(self) -> None:
        with self._lock:
            self._listeners.clear()
        self._stop_capture()

    # ---- internals -------------------------------------------------------------

    def _authenticate(self, pkt: proto.Listen) -> str | None:
        if self.pairing is None:
            return None
        for key_hash, entry in self.pairing.devices.items():
            mac = proto.listen_mac(bytes.fromhex(key_hash), pkt.session, pkt.bitrate_kbps,
                                   pkt.counter)
            if hmac.compare_digest(mac, pkt.mac):
                with self._lock:
                    if pkt.counter <= self._counters.get(key_hash, -1):
                        return None  # replayed
                    self._counters[key_hash] = pkt.counter
                return entry.get("device", "Phone")
        return None

    def _ensure_capture(self) -> bool:
        if self._capture is not None:
            return True
        try:
            capture = self._capture_factory(self._on_frame, on_error=self._on_capture_error)
            capture.start()
        except CaptureError as exc:
            self.capture_error = str(exc)
            log.warning("can't capture this PC's audio: %s", exc)
            return False
        self.capture_error = None
        self._capture = capture
        return True

    def _on_capture_error(self, message: str) -> None:
        self.capture_error = message
        log.warning("capture stopped: %s", message)
        with self._lock:
            listeners = list(self._listeners.items())
            self._listeners.clear()
        for addr, listener in listeners:
            self._send(proto.Refuse(listener.info.session, proto.REFUSE_NO_CAPTURE), addr)
        self._capture = None
        self._changed()

    def _after_removal(self) -> None:
        with self._lock:
            empty = not self._listeners
        if empty:
            self._stop_capture()
        self._changed()

    def _stop_capture(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            capture.stop()

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def _on_frame(self, pcm: np.ndarray, capture_ms: int = 0) -> None:
        """Capture thread: one 10 ms frame of int16 stereo for every listener."""
        now = self._clock()
        with self._lock:
            listeners = list(self._listeners.values())
        for l in listeners:
            if l.applied_kbps != l.info.bitrate_kbps:
                l.encoder.set_bitrate(l.info.bitrate_kbps * 1000)
                l.applied_kbps = l.info.bitrate_kbps
            if now >= l.next_hello:
                self._send(proto.Hello(l.stream_session, 48_000, 2, 10, l.info.bitrate_kbps,
                                       proto.SOURCE_PC, capture_ms, self.name), l.info.addr)
                l.next_hello = now + self.HELLO_INTERVAL_S
            packet = l.encoder.encode(pcm)
            self._send(proto.Audio(l.stream_session, l.seq, packet, l.prev), l.info.addr)
            l.prev = packet
            l.seq += 1
