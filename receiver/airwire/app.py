"""Wires the receiver together and exposes one status snapshot for the UI / CLI."""

from __future__ import annotations

import json
import logging
import os
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import protocol as proto
from .jitter_buffer import JitterBuffer
from .net import ReceiverServer
from .player import AudioPlayer, list_output_devices
from .stats import NetStats

log = logging.getLogger(__name__)

SOURCE_LABELS = {proto.SOURCE_INTERNAL: "Internal audio", proto.SOURCE_MIC: "Microphone"}


@dataclass
class Settings:
    volume: int = 80
    device_name: str = ""

    @staticmethod
    def path() -> Path:
        base = os.environ.get("APPDATA") or str(Path.home() / ".config")
        return Path(base) / "Airwire" / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        try:
            path = self.path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("could not save settings: %s", exc)


@dataclass
class Status:
    listening: str
    connected: bool
    sender_name: str = ""
    sender_addr: str = ""
    source: str = ""
    bitrate_kbps: int = 0
    channels: int = 0
    playing: bool = False
    est_latency_ms: float | None = None
    buffer_ms: float = 0.0
    target_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_pct: float = 0.0
    rtt_ms: float | None = None
    kbps: float = 0.0
    recovered: int = 0
    concealed: int = 0
    output_latency_ms: float = 0.0
    output_device: str = ""
    output_error: str | None = None
    local_ips: list[str] = field(default_factory=list)


def local_ipv4_addresses() -> list[str]:
    """LAN addresses of this PC, primary route first (connect() on UDP sends nothing)."""
    ordered: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ordered.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ordered:
                ordered.append(ip)
    except OSError:
        pass
    return [ip for ip in ordered if not ip.startswith(("127.", "169.254."))]


class ReceiverApp:
    def __init__(self, port: int = proto.DEFAULT_PORT, host: str = "0.0.0.0",
                 name: str | None = None):
        self.stats = NetStats()
        self.jitter = JitterBuffer()
        self.server = ReceiverServer(self.jitter, self.stats, port=port, host=host, name=name)
        self.player = AudioPlayer(self.jitter.pop)
        self._local_ips = local_ipv4_addresses()

    def start(self, device: int | None = None) -> None:
        self.server.start()
        self.player.start(device)

    def stop(self) -> None:
        self.player.stop()
        self.server.stop()

    def set_device(self, device: int | None) -> None:
        self.player.start(device)

    def output_device_name(self) -> str:
        idx = self.player.device
        for d in list_output_devices():
            if d.index == idx or (idx is None and d.is_default):
                return d.name
        return ""

    def status(self) -> Status:
        sender = self.server.sender
        jb, st = self.jitter, self.stats
        status = Status(
            listening=f"{self.server.host}:{self.server.port}",
            connected=sender is not None,
            playing=jb.playing,
            buffer_ms=jb.buffer_ms,
            target_ms=jb.target_ms,
            output_latency_ms=self.player.latency_ms,
            output_error=self.player.error,
            recovered=jb.stats.recovered,
            concealed=jb.stats.fec + jb.stats.concealed,
            local_ips=self._local_ips,
        )
        if sender is None:
            return status
        status.sender_name = sender.name
        status.sender_addr = f"{sender.addr[0]}:{sender.addr[1]}"
        status.source = SOURCE_LABELS.get(sender.source, "") if sender.source is not None else ""
        status.bitrate_kbps = sender.bitrate_kbps
        status.channels = sender.channels
        status.jitter_ms = st.jitter_ms
        status.loss_pct = st.loss * 100
        status.rtt_ms = st.rtt_ms
        status.kbps = st.kbps
        if jb.playing:
            # A frame waits (depth - 1) frames in the buffer; its own duration is the
            # frame_ms the sender spent filling it.
            one_way = st.rtt_ms / 2 if st.rtt_ms is not None else 0.0
            queued = max(0.0, jb.buffer_ms - jb.frame_ms)
            status.est_latency_ms = (sender.capture_ms + jb.frame_ms + one_way
                                     + queued + self.player.latency_ms)
        return status
