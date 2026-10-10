"""python -m airwire [--port 50005] [--device NAME|INDEX] [--headless]"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import time

from . import protocol as proto
from .app import ReceiverApp, Settings
from .opus import OpusError, version as opus_version
from .player import find_device, list_output_devices


def _print_devices() -> None:
    for d in list_output_devices():
        mark = "*" if d.is_default else " "
        print(f"{mark} {d.index:3d}  {d.name}  [{d.hostapi}]")


def _run_headless(app: ReceiverApp) -> None:
    print(f"Listening on {app.server.host}:{app.server.port}  (this PC: "
          f"{', '.join(app.status().local_ips) or 'n/a'})  Ctrl+C to quit")
    if app.files:
        app.files.on_event = lambda t: print(
            f"file {'from' if t.direction == 'in' else 'to'} phone: {t.name} {t.status}"
            + (f" ({t.error})" if t.error else ""), flush=True)
        print(f"File transfer: pairing code {app.files.pairing.code}, saving to "
              f"{app.files.files_dir}", flush=True)
    while True:
        time.sleep(1)
        s = app.status()
        if not s.connected:
            line = "waiting for sender..."
        else:
            est = f"{s.est_latency_ms:.0f}" if s.est_latency_ms is not None else "-"
            rtt = f"{s.rtt_ms:.1f}" if s.rtt_ms is not None else "-"
            line = (f"{s.sender_name or s.sender_addr} | est {est} ms | buf {s.buffer_ms:.0f}/"
                    f"{s.target_ms:.0f} ms | jitter {s.jitter_ms:.1f} ms | loss "
                    f"{s.loss_pct:.1f}% | rtt {rtt} ms | rec/conc {s.recovered}/{s.concealed}")
        print(line, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="airwire",
                                     description="Receive Opus audio from the Android sender.")
    parser.add_argument("--port", type=int, default=proto.DEFAULT_PORT)
    parser.add_argument("--bind", default="0.0.0.0", help="local address to listen on")
    parser.add_argument("--device", help="output device index or part of its name")
    parser.add_argument("--volume", type=int, help="0-100 (default: last used)")
    parser.add_argument("--name", default=socket.gethostname(), help="name shown on the phone")
    parser.add_argument("--headless", action="store_true", help="no window, print stats")
    parser.add_argument("--files-dir", help="where files from the phone are saved "
                                             "(default: Downloads/Airwire)")
    parser.add_argument("--no-files", action="store_true", help="disable file transfer")
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list_devices:
        _print_devices()
        return 0

    try:
        logging.info("libopus %s", opus_version())
    except OpusError as exc:
        print(exc, file=sys.stderr)
        return 1

    settings = Settings.load()
    if args.volume is not None:
        settings.volume = max(0, min(100, args.volume))
    device = find_device(args.device) if args.device else find_device(settings.device_name)
    if args.device and device is None:
        print(f"Output device {args.device!r} not found. Available:", file=sys.stderr)
        _print_devices()
        return 2

    if args.files_dir:
        settings.files_dir = args.files_dir
    try:
        app = ReceiverApp(port=args.port, host=args.bind, name=args.name, settings=settings,
                          enable_files=not args.no_files)
    except OSError as exc:
        print(f"Cannot listen on port {args.port} (UDP audio / TCP files): {exc}",
              file=sys.stderr)
        return 1
    app.player.volume = settings.volume / 100
    app.start(device)

    try:
        if args.headless:
            _run_headless(app)
        else:
            from . import ui
            ui.run(app, settings)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
