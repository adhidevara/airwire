"""UDP listener: discovery replies, sender locking, audio intake, PING/PONG.

Packets from phones that listen to this PC (LISTEN, their PING and BYE) go to the
broadcaster, which sends the PC's audio from this same socket.
"""

from __future__ import annotations

import dataclasses
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import protocol as proto
from .jitter_buffer import JitterBuffer
from .stats import NetStats

if TYPE_CHECKING:
    from .broadcast import PcAudioBroadcaster

log = logging.getLogger(__name__)


@dataclass
class SenderInfo:
    addr: tuple[str, int]
    session: int
    connected_at: float
    last_seen: float
    name: str = ""
    source: int | None = None
    bitrate_kbps: int = 0
    channels: int = 0
    frame_ms: int = 10
    capture_ms: int = 0


class ReceiverServer:
    PING_INTERVAL_S = 0.5
    # Busy Wi-Fi can hold packets for several seconds; don't drop the sender for that.
    TIMEOUT_S = 5.0

    def __init__(self, jitter: JitterBuffer, stats: NetStats, *, port: int = proto.DEFAULT_PORT,
                 host: str = "0.0.0.0", name: str | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.jitter = jitter
        self.stats = stats
        self.name = name or socket.gethostname()
        self._clock = clock
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        self.sock.bind((host, port))
        self.sock.settimeout(0.1)
        self.host = host
        self.port = self.sock.getsockname()[1]
        self.invalid_packets = 0
        self.foreign_packets = 0
        self._sender: SenderInfo | None = None
        # (addr, session) of a sender that timed out, so its return keeps the buffer history
        self._last_session: tuple[tuple[str, int], int] | None = None
        self.broadcaster: PcAudioBroadcaster | None = None
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run, name="udp-receiver", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        self.sock.close()

    @property
    def sender(self) -> SenderInfo | None:
        with self._lock:
            return dataclasses.replace(self._sender) if self._sender else None

    def sender_silence_s(self) -> float:
        """Seconds since the locked sender's last packet (0 without a sender)."""
        with self._lock:
            return self._clock() - self._sender.last_seen if self._sender else 0.0

    # ---- loop ----------------------------------------------------------------

    def _run(self) -> None:
        next_tick = 0.0
        while self._running:
            data = addr = None
            try:
                data, addr = self.sock.recvfrom(4096)
            except socket.timeout:
                pass
            except ConnectionResetError:
                # Windows reports ICMP "port unreachable" from an earlier PING here.
                pass
            except OSError:
                if not self._running:
                    break
                log.exception("socket error")
                time.sleep(0.1)
            now = self._clock()
            if data:
                self._handle(data, addr, now)
            if now >= next_tick:
                self._tick(now)
                next_tick = now + self.PING_INTERVAL_S

    def send(self, packet: proto.Packet, addr: tuple[str, int]) -> None:
        try:
            self.sock.sendto(proto.encode(packet), addr)
        except OSError as exc:
            log.debug("send to %s failed: %s", addr, exc)

    def _handle(self, data: bytes, addr: tuple[str, int], now: float) -> None:
        pkt = proto.decode(data)
        match pkt:
            case None:
                self.invalid_packets += 1
            case proto.Audio(session, seq, primary, redundant):
                if self._accept(addr, session, now) is None:
                    self.foreign_packets += 1
                    return
                self.jitter.push(seq, primary, redundant)
                self.stats.on_audio(seq, len(data), now)
            case proto.Hello():
                sender = self._accept(addr, pkt.session, now)
                if sender is None:
                    self.foreign_packets += 1
                    return
                with self._lock:
                    sender.name = pkt.name
                    sender.source = pkt.source
                    sender.bitrate_kbps = pkt.bitrate_kbps
                    sender.channels = pkt.channels
                    sender.frame_ms = pkt.frame_ms
                    sender.capture_ms = pkt.capture_ms
                self.stats.frame_ms = pkt.frame_ms
            case proto.Discover(nonce):
                self.send(proto.Offer(nonce, self.port, self.name), addr)
            case proto.Pong(t_ns):
                if self._is_sender(addr):
                    self.stats.on_pong(t_ns)
            case proto.Bye():
                if self._is_sender(addr):
                    log.info("sender %s said bye", addr)
                    self._disconnect()
                elif self.broadcaster:
                    self.broadcaster.handle_bye(pkt, addr)
            case proto.Listen():
                if self.broadcaster:
                    self.broadcaster.handle_listen(pkt, addr)
                else:
                    self.send(proto.Refuse(pkt.session, proto.REFUSE_DISABLED), addr)
            case proto.Ping():
                if self.broadcaster:
                    self.broadcaster.handle_ping(pkt, addr)

    def _is_sender(self, addr: tuple[str, int]) -> bool:
        with self._lock:
            return self._sender is not None and self._sender.addr == addr

    def _accept(self, addr: tuple[str, int], session: int, now: float) -> SenderInfo | None:
        """Lock onto the first sender; return its info, or None for a foreign sender."""
        with self._lock:
            s = self._sender
            if s is None or (s.addr != addr and now - s.last_seen > self.TIMEOUT_S):
                resumed = self._last_session == (addr, session)
                log.info("sender %s: %s:%d", "is back" if resumed else "connected", *addr)
                s = self._sender = SenderInfo(addr, session, connected_at=now, last_seen=now)
                self.jitter.reset(keep_history=resumed)
                self.stats.reset()
            elif s.addr != addr:
                return None
            elif s.session != session:
                log.info("sender %s started a new session", addr)
                s.session = session
                self.jitter.reset()
                self.stats.reset()
            s.last_seen = now
            return s

    def _disconnect(self, keep_history: bool = False) -> None:
        with self._lock:
            s = self._sender
            self._last_session = (s.addr, s.session) if keep_history and s else None
            self._sender = None
        self.jitter.reset(keep_history=keep_history)
        self.stats.reset()

    def _tick(self, now: float) -> None:
        if self.broadcaster:
            self.broadcaster.tick()
        with self._lock:
            sender = self._sender
            timed_out = sender is not None and now - sender.last_seen > self.TIMEOUT_S
        if sender is None:
            return
        if timed_out:
            log.info("sender %s timed out", sender.addr)
            self._disconnect(keep_history=True)
            return
        self.stats.sample(now)
        ping = proto.Ping(
            t_ns=time.monotonic_ns(),
            loss_permille=round(self.stats.loss * 1000),
            jitter_us=round(self.stats.jitter_ms * 1000),
            buffer_ms=round(self.jitter.buffer_ms),
        )
        self.send(ping, sender.addr)
