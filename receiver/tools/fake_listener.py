"""Simulates a phone listening to this PC's audio (PC -> phone), so it can be tested
without the app.

    python tools/fake_listener.py --host 127.0.0.1 --code 123456     # pair, then listen
    python tools/fake_listener.py --host 192.168.1.15 --token <token> --play

Pairs with the code shown in the receiver window (or reuses --token), sends LISTEN every
second, plays or just buffers the audio, reports stats with PING, and says BYE on exit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from airwire import protocol as p  # noqa: E402
from airwire.jitter_buffer import JitterBuffer  # noqa: E402
from airwire.player import AudioPlayer  # noqa: E402
from airwire.stats import NetStats  # noqa: E402


def pair(host: str, port: int, code: str) -> str:
    req = urllib.request.Request(f"http://{host}:{port}/api/v1/pair", method="POST",
                                 data=json.dumps({"code": code, "device": "fake-listener"}).encode())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())["token"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", required=True, help="PC running the receiver")
    ap.add_argument("--port", type=int, default=p.DEFAULT_PORT)
    ap.add_argument("--code", help="pairing code shown on the PC")
    ap.add_argument("--token", help="token from an earlier pairing")
    ap.add_argument("--bitrate", type=int, default=128, help="kbps")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = forever)")
    ap.add_argument("--play", action="store_true", help="play through this machine's speakers")
    args = ap.parse_args()

    token = args.token or (pair(args.host, args.port, args.code) if args.code else None)
    if not token:
        print("pass --code (pairing code on the PC) or --token")
        return 1
    if args.code:
        print(f"paired; reuse with --token {token}")
    key = hashlib.sha256(token.encode()).digest()

    addr = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(0.2)
    session = random.randrange(1, 1 << 16)
    jitter, stats = JitterBuffer(), NetStats()
    player = AudioPlayer(jitter.pop)
    player.volume = 1.0 if args.play else 0.0
    player.start()
    running = True
    state = {"stream": None, "hello": None, "refused": None}

    def receive() -> None:
        while running:
            try:
                data, _ = sock.recvfrom(4096)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return
            now = time.monotonic()
            pkt = p.decode(data)
            match pkt:
                case p.Audio(s, seq, primary, redundant):
                    if s != state["stream"]:
                        state["stream"] = s
                        jitter.reset()
                        stats.reset()
                    jitter.push(seq, primary, redundant)
                    stats.on_audio(seq, len(data), now)
                case p.Hello():
                    state["hello"] = pkt
                case p.Pong(t_ns):
                    stats.on_pong(t_ns)
                case p.Refuse(_, reason):
                    state["refused"] = reason

    threading.Thread(target=receive, daemon=True).start()
    start = time.monotonic()
    next_listen = next_ping = next_report = start
    reasons = {p.REFUSE_NOT_PAIRED: "not paired", p.REFUSE_DISABLED: "listening is off on the PC",
               p.REFUSE_NO_CAPTURE: "the PC can't capture its audio", p.REFUSE_STOPPED: "stopped on the PC"}
    try:
        while not args.seconds or time.monotonic() - start < args.seconds:
            now = time.monotonic()
            if state["refused"] is not None:
                print("refused:", reasons.get(state["refused"], state["refused"]))
                return 2
            if now >= next_listen:
                counter = time.time_ns() // 1_000_000
                mac = p.listen_mac(key, session, args.bitrate, counter)
                sock.sendto(p.encode(p.Listen(session, args.bitrate, counter, mac, "fake-listener")), addr)
                next_listen = now + 1.0
            if now >= next_ping:
                stats.sample(now)
                sock.sendto(p.encode(p.Ping(time.monotonic_ns(), round(stats.loss * 1000),
                                            round(stats.jitter_ms * 1000), round(jitter.buffer_ms))), addr)
                next_ping = now + 0.5
            if now >= next_report:
                h = state["hello"]
                js = jitter.stats
                rtt = f"{stats.rtt_ms:.1f}" if stats.rtt_ms is not None else "n/a"
                print(f"{h.name if h else '?'} | {stats.received} pkts loss {stats.loss * 100:.1f}% "
                      f"jitter {stats.jitter_ms:.1f} ms rtt {rtt} ms | buffer {jitter.buffer_ms:.0f} ms "
                      f"wait {jitter.wait_ms:.1f} ms | underruns {js.underruns} concealed {js.concealed}",
                      flush=True)
                next_report = now + 1.0
            time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        running = False
        sock.sendto(p.encode(p.Bye(session)), addr)
        sock.close()
        player.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
