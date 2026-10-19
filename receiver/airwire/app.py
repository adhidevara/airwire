"""Wires the receiver together and exposes one status snapshot for the UI / CLI."""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import protocol as proto
from .broadcast import ListenerInfo, PcAudioBroadcaster
from .files import FileService, Pairing, default_files_dir
from .jitter_buffer import JitterBuffer
from .net import ReceiverServer
from .player import AudioPlayer, list_output_devices
from .stats import NetStats

log = logging.getLogger(__name__)

SOURCE_LABELS = {proto.SOURCE_INTERNAL: "Internal audio", proto.SOURCE_MIC: "Microphone"}


_SETTINGS_LOCK = threading.Lock()


@dataclass
class Settings:
    volume: int = 80
    device_name: str = ""
    files_dir: str = ""
    pair_code: str = ""
    allow_listen: bool = True   # paired phones may play this PC's audio
    # sha256(token) -> {"device", "paired_at"}; see files.Pairing
    paired_devices: dict = field(default_factory=dict)

    @staticmethod
    def path() -> Path:
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        elif os.environ.get("APPDATA"):
            base = Path(os.environ["APPDATA"])
        else:
            base = Path.home() / ".config"
        return base / "Airwire" / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            data = json.loads(cls.path().read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        with _SETTINGS_LOCK:
            try:
                path = self.path()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
            except OSError as exc:
                log.warning("could not save settings: %s", exc)

    def resolved_files_dir(self) -> Path:
        return Path(self.files_dir) if self.files_dir else default_files_dir()


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
    silent_s: float = 0.0          # since the sender's last packet
    local_ips: list[str] = field(default_factory=list)
    listeners: list[ListenerInfo] = field(default_factory=list)
    allow_listen: bool = True
    capture_error: str | None = None


def local_ipv4_addresses() -> list[str]:
    """LAN addresses of this host, primary route first (connect() on UDP sends nothing)."""
    ordered: list[str] = []
    for probe in [("10.255.255.255", 1), ("1.1.1.1", 80)]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(probe)
                ip = s.getsockname()[0]
                if ip not in ordered:
                    ordered.append(ip)
                break
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
                 name: str | None = None, settings: Settings | None = None,
                 enable_files: bool = True):
        # Only settings loaded from disk are written back; an ad-hoc app (tests,
        # screenshots) must not overwrite the user's pairing and preferences.
        self._persist = settings is not None
        self.settings = settings or Settings()
        self.stats = NetStats()
        self.jitter = JitterBuffer()
        self.server = ReceiverServer(self.jitter, self.stats, port=port, host=host, name=name)
        self.player = AudioPlayer(self.jitter.pop)
        self.files: FileService | None = None
        if enable_files:
            pairing = Pairing(self.settings.pair_code, self.settings.paired_devices,
                              on_change=self._pairing_changed)
            self._pairing_changed(pairing)
            try:
                # TCP on the same port number as the UDP audio stream.
                self.files = FileService(self.settings.resolved_files_dir(), pairing,
                                         host=host, port=self.server.port, name=self.server.name)
            except OSError:
                self.server.stop()
                raise
        self.broadcast = PcAudioBroadcaster(
            self.files.pairing if self.files else None, self.server.send,
            name=self.server.name, enabled=self.settings.allow_listen)
        self.server.broadcaster = self.broadcast
        self._local_ips = local_ipv4_addresses()

    def set_allow_listen(self, allow: bool) -> None:
        self.settings.allow_listen = self.broadcast.enabled = allow
        if not allow:
            self.broadcast.stop_all()
        if self._persist:
            self.settings.save()

    def _pairing_changed(self, pairing: Pairing) -> None:
        self.settings.pair_code = pairing.code
        self.settings.paired_devices = pairing.devices
        if self._persist:
            self.settings.save()

    def start(self, device: int | None = None) -> None:
        self.server.start()
        if self.files:
            self.files.start()
        self.player.start(device)

    def stop(self) -> None:
        self.broadcast.close()
        self.player.stop()
        if self.files:
            self.files.stop()
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
            listeners=self.broadcast.listeners(),
            allow_listen=self.broadcast.enabled,
            capture_error=self.broadcast.capture_error,
        )
        if sender is None:
            return status
        status.sender_name = sender.name
        status.silent_s = self.server.sender_silence_s()
        status.sender_addr = f"{sender.addr[0]}:{sender.addr[1]}"
        status.source = SOURCE_LABELS.get(sender.source, "") if sender.source is not None else ""
        status.bitrate_kbps = sender.bitrate_kbps
        status.channels = sender.channels
        status.jitter_ms = st.jitter_ms
        status.loss_pct = st.loss * 100
        status.rtt_ms = st.rtt_ms
        status.kbps = st.kbps
        if jb.playing:
            # A frame waits wait_ms in the buffer; its own duration is the frame_ms the
            # sender spent filling it.
            one_way = st.rtt_ms / 2 if st.rtt_ms is not None else 0.0
            status.est_latency_ms = (sender.capture_ms + jb.frame_ms + one_way
                                     + jb.wait_ms + self.player.latency_ms)
        return status
