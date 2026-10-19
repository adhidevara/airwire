"""Simulates the Android sender so the receiver can be tested without a phone.

    python tools/fake_sender.py --host 127.0.0.1                  # clean tone
    python tools/fake_sender.py --host 127.0.0.1 --loss 5 --jitter 15
    python tools/fake_sender.py --wav music_48k.wav --loss 2 --burst 3
    python tools/fake_sender.py --host 127.0.0.1 --chunk 2 --stall 60 --stall-every 5
    python tools/fake_sender.py --discover                         # find receivers only

Speaks the same protocol as the app (PROTOCOL.md): HELLO every second, AUDIO every
10 ms with the previous frame as a redundant copy, PONG replies, BYE on exit.
"""

from __future__ import annotations

import argparse
import heapq
import itertools
import random
import socket
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Iterator

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airwire import protocol as p  # noqa: E402
from airwire.opus import OpusEncoder  # noqa: E402

RATE = 48_000
FRAME = 480


def tone(channels: int, freq: float) -> Iterator[np.ndarray]:
    """Two alternating notes (0.5 s each) so drop-outs and clicks are easy to hear."""
    phase = 0.0
    for i in itertools.count():
        f = freq if (i // 50) % 2 == 0 else freq * 1.5
        step = 2 * np.pi * f / RATE
        ph = phase + step * np.arange(1, FRAME + 1)
        phase = float(ph[-1] % (2 * np.pi))
        mono = (6000 * np.sin(ph)).astype(np.int16)
        yield np.repeat(mono[:, None], channels, axis=1)


def wav_frames(path: str, channels: int) -> Iterator[np.ndarray]:
    with wave.open(path, "rb") as w:
        if w.getframerate() != RATE or w.getsampwidth() != 2:
            sys.exit(f"{path}: need 48 kHz 16-bit PCM (got {w.getframerate()} Hz, "
                     f"{8 * w.getsampwidth()}-bit)")
        src_ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, src_ch)
    if src_ch == 1 and channels == 2:
        data = np.repeat(data, 2, axis=1)
    elif src_ch >= 2 and channels == 1:
        data = data[:, :2].mean(axis=1, dtype=np.int32).astype(np.int16)[:, None]
    else:
        data = data[:, :channels]
    usable = len(data) // FRAME * FRAME
    if usable == 0:
        sys.exit(f"{path}: file is shorter than one frame")
    while True:
        for start in range(0, usable, FRAME):
            yield data[start:start + FRAME]


def discover(port: int, timeout: float = 1.5) -> list[tuple[str, p.Offer]]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", 0))
    nonce = random.getrandbits(32)
    msg = p.encode(p.Discover(nonce))
    for target in ("255.255.255.255", "127.0.0.1"):
        try:
            sock.sendto(msg, (target, port))
        except OSError as exc:
            print(f"broadcast to {target} failed: {exc}")
    found: dict[str, p.Offer] = {}
    end = time.monotonic() + timeout
    while (left := end - time.monotonic()) > 0:
        sock.settimeout(left)
        try:
            data, addr = sock.recvfrom(1500)
        except (socket.timeout, ConnectionResetError):
            continue
        offer = p.decode(data)
        if isinstance(offer, p.Offer) and offer.nonce == nonce:
            found[addr[0]] = offer
    sock.close()
    return list(found.items())


class FakeSender:
    def __init__(self, args: argparse.Namespace, host: str):
        self.args = args
        self.addr = (host, args.port)
        self.channels = 1 if args.mono else 2
        self.session = random.randrange(1, 1 << 16)
        self.encoder = OpusEncoder(channels=self.channels, bitrate=args.bitrate * 1000)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.2)
        self.running = True
        self.last_ping: p.Ping | None = None
        self.sent = self.dropped = 0

    def _hello(self) -> bytes:
        return p.encode(p.Hello(self.session, RATE, self.channels, 10, self.args.bitrate,
                                p.SOURCE_MIC if self.args.mono else p.SOURCE_INTERNAL,
                                capture_ms=0, name="fake-sender"))

    def _listen(self) -> None:
        while self.running:
            try:
                data, addr = self.sock.recvfrom(1500)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return
            pkt = p.decode(data)
            if isinstance(pkt, p.Ping):
                self.last_ping = pkt
                self.sock.sendto(p.encode(p.Pong(pkt.t_ns)), addr)

    def run(self, frames: Iterator[np.ndarray]) -> None:
        a = self.args
        threading.Thread(target=self._listen, daemon=True).start()
        pending: list[tuple[float, int, bytes]] = []  # (send_at, seq, datagram)
        prev: bytes | None = None
        burst_left = 0
        t0 = time.perf_counter()
        next_stall = t0 + a.stall_every if a.stall else None
        stall_until = 0.0
        next_hello = next_report = t0
        total = int(a.seconds * 100) if a.seconds else None
        seq = 0
        print(f"streaming to {self.addr[0]}:{self.addr[1]} session={self.session} "
              f"loss={a.loss}% burst={a.burst} jitter=0..{a.jitter}ms chunk={a.chunk} "
              f"stall={a.stall}ms/{a.stall_every}s  Ctrl+C to stop")
        while total is None or seq < total or pending:
            now = time.perf_counter()
            if now >= next_hello:
                self.sock.sendto(self._hello(), self.addr)
                next_hello += 1.0
            if now >= next_report:
                self._report()
                next_report += 1.0
            # Frames leave in groups of `chunk`, once the last one of the group is captured
            # (like an Android capture buffer that hands over 20-40 ms at a time).
            due = t0 + (seq // a.chunk * a.chunk + a.chunk - 1) * FRAME / RATE
            if next_stall is not None and now >= next_stall:
                stall_until = now + a.stall / 1000  # Wi-Fi hiccup: hold everything, then burst
                next_stall += a.stall_every
            if (total is None or seq < total) and now >= due:
                packet = self.encoder.encode(next(frames))
                red = prev if not a.no_redundancy else None
                data = p.encode(p.Audio(self.session, seq, packet, red))
                prev = packet
                if burst_left > 0 or random.random() * 100 < a.loss:
                    burst_left = (burst_left or a.burst) - 1
                    self.dropped += 1
                else:
                    delay = random.uniform(0, a.jitter) / 1000 if a.jitter else 0.0
                    heapq.heappush(pending, (max(now + delay, stall_until), seq, data))
                seq += 1
                continue
            while pending and pending[0][0] <= now and now >= stall_until:
                _, _, data = heapq.heappop(pending)
                self.sock.sendto(data, self.addr)
                self.sent += 1
            wake = [due] if total is None or seq < total else []
            if pending:
                wake.append(max(pending[0][0], stall_until))
            if wake:
                time.sleep(max(0.0, min(wake) - time.perf_counter()))

    def _report(self) -> None:
        ping = self.last_ping
        rx = (f" | receiver: loss {ping.loss_permille / 10:.1f}% jitter "
              f"{ping.jitter_us / 1000:.1f} ms buffer {ping.buffer_ms} ms") if ping else ""
        print(f"sent {self.sent} dropped {self.dropped}{rx}", flush=True)

    def close(self) -> None:
        self.running = False
        try:
            self.sock.sendto(p.encode(p.Bye(self.session)), self.addr)
        finally:
            self.sock.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", help="receiver IP (default: auto-discover)")
    ap.add_argument("--port", type=int, default=p.DEFAULT_PORT)
    ap.add_argument("--wav", help="48 kHz 16-bit WAV to loop instead of a tone")
    ap.add_argument("--freq", type=float, default=440.0)
    ap.add_argument("--mono", action="store_true", help="encode 1 channel (like the mic mode)")
    ap.add_argument("--bitrate", type=int, default=128, help="kbps")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = forever)")
    ap.add_argument("--loss", type=float, default=0, help="random packet loss in %%")
    ap.add_argument("--burst", type=int, default=1, help="packets lost per loss event")
    ap.add_argument("--jitter", type=float, default=0, help="max extra delay per packet in ms")
    ap.add_argument("--chunk", type=int, default=1,
                    help="send frames in groups of N, like a bursty capture buffer")
    ap.add_argument("--stall", type=float, default=0,
                    help="every --stall-every seconds, hold all packets for N ms")
    ap.add_argument("--stall-every", type=float, default=5.0)
    ap.add_argument("--no-redundancy", action="store_true")
    ap.add_argument("--discover", action="store_true", help="only list receivers and exit")
    args = ap.parse_args()

    if args.discover or not args.host:
        offers = discover(args.port)
        for ip, offer in offers:
            print(f"found receiver {offer.name!r} at {ip}:{offer.audio_port}")
        if args.discover:
            return 0 if offers else 1
        if not offers:
            print("no receiver found; pass --host")
            return 1
        args.host = offers[0][0]
        args.port = offers[0][1].audio_port

    channels = 1 if args.mono else 2
    frames = wav_frames(args.wav, channels) if args.wav else tone(channels, args.freq)
    sender = FakeSender(args, args.host)
    try:
        sender.run(frames)
    except KeyboardInterrupt:
        pass
    finally:
        sender.close()
        sender._report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
