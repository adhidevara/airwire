"""File transfer between the phone and this PC.

HTTP over TCP, on the same port number as the UDP audio stream (50005). See PROTOCOL.md.

    POST   /api/v1/pair             {"code", "device"} -> {"token", "name"}
    GET    /api/v1/hello            -> {"name", "device"}
    PUT    /api/v1/files/<name>     phone -> PC, raw body with Content-Length
    GET    /api/v1/outbox?since=&wait=   long-poll for files queued for the phone
    GET    /api/v1/outbox/<id>      PC -> phone download
    DELETE /api/v1/outbox/<id>      phone confirms it saved the file

Everything except /pair needs `Authorization: Bearer <token>`. A phone gets its token by
sending the 6-digit pairing code shown in the receiver window.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, quote, unquote, urlsplit

log = logging.getLogger(__name__)

API = "/api/v1"
CHUNK = 256 * 1024
MAX_WAIT_S = 30
FREE_SPACE_MARGIN = 64 * 1024 * 1024
MAX_HISTORY = 200


def default_files_dir() -> Path:
    return Path.home() / "Downloads" / "Airwire"


# ---- file names ---------------------------------------------------------------

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, max_len: int = 180) -> str:
    """A name that is safe to create inside the target folder on Windows and Unix."""
    name = name.replace("\\", "/").split("/")[-1]
    name = _INVALID.sub("_", name).strip().rstrip(". ")
    if not name or set(name) == {"."}:
        name = "file"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    if len(name) > max_len:
        p = Path(name)
        suffix = p.suffix if len(p.suffix) < 16 else ""
        name = p.stem[: max_len - len(suffix)] + suffix
    return name


def unique_path(folder: Path, name: str) -> Path:
    path = folder / name
    if not path.exists():
        return path
    p = Path(name)
    for n in range(1, 10_000):
        candidate = folder / f"{p.stem} ({n}){p.suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{p.stem} ({uuid.uuid4().hex[:8]}){p.suffix}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


# ---- pairing ------------------------------------------------------------------

class PairingError(Exception):
    status = 403


class PairingLocked(PairingError):
    status = 429


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Pairing:
    """The 6-digit code shown on the PC, and the phones that paired with it.

    Only SHA-256 hashes of tokens are kept, so the settings file can't be used to
    impersonate a phone.
    """

    MAX_FAILURES = 5
    WINDOW_S = 60.0

    def __init__(self, code: str = "", devices: dict[str, dict] | None = None,
                 on_change: Callable[["Pairing"], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.code = code if re.fullmatch(r"\d{6}", code or "") else self.generate_code()
        self._devices: dict[str, dict] = dict(devices or {})
        self._on_change = on_change
        self._clock = clock
        self._failures: deque[float] = deque()
        self._lock = threading.Lock()

    @staticmethod
    def generate_code() -> str:
        return f"{secrets.randbelow(10**6):06d}"

    @property
    def devices(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._devices)

    def device_names(self) -> list[str]:
        return [d.get("device", "Phone") for d in self.devices.values()]

    def pair(self, code: str, device: str) -> str:
        with self._lock:
            now = self._clock()
            while self._failures and now - self._failures[0] > self.WINDOW_S:
                self._failures.popleft()
            if len(self._failures) >= self.MAX_FAILURES:
                raise PairingLocked("too many wrong codes, wait a minute")
            if not hmac.compare_digest(re.sub(r"\D", "", code or ""), self.code):
                self._failures.append(now)
                raise PairingError("wrong pairing code")
            token = secrets.token_urlsafe(32)
            self._devices[_token_hash(token)] = {
                "device": (device or "Phone")[:64],
                "paired_at": datetime.now().isoformat(timespec="seconds"),
            }
        self._changed()
        return token

    def device_for(self, token: str) -> str | None:
        if not token:
            return None
        entry = self.devices.get(_token_hash(token))
        return entry.get("device", "Phone") if entry else None

    def reset(self) -> None:
        """New code, and every paired phone has to pair again."""
        with self._lock:
            self.code = self.generate_code()
            self._devices.clear()
            self._failures.clear()
        self._changed()

    def _changed(self) -> None:
        if self._on_change:
            self._on_change(self)


# ---- transfers ----------------------------------------------------------------

@dataclass
class Transfer:
    id: str
    direction: str                 # "in": phone -> PC, "out": PC -> phone
    name: str
    size: int
    done: int = 0
    status: str = "queued"         # queued | transferring | done | failed
    device: str = ""
    path: str = ""
    sha256: str = ""
    error: str = ""
    updated: float = field(default_factory=time.time)

    @property
    def progress(self) -> float:
        return 1.0 if self.size == 0 else min(1.0, self.done / self.size)


class InsufficientStorage(Exception):
    pass


class FileService:
    def __init__(self, files_dir: Path, pairing: Pairing, *, host: str = "0.0.0.0",
                 port: int = 50005, name: str | None = None):
        self.files_dir = Path(files_dir)
        self.pairing = pairing
        self.name = name or socket.gethostname()
        self._transfers: dict[str, Transfer] = {}
        self._outbox: dict[str, Transfer] = {}       # ready to be downloaded by the phone
        self._outbox_version = 0
        self._cond = threading.Condition()
        self._save_lock = threading.Lock()
        self.on_event: Callable[[Transfer], None] | None = None
        self.httpd = _Server((host, port), _Handler, self)
        self.host = host
        self.port = self.httpd.server_address[1]
        self._thread: threading.Thread | None = None

    # ---- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="file-server",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cond:
            self._cond.notify_all()
        self.httpd.shutdown()
        self.httpd.server_close()

    # ---- state for the UI ------------------------------------------------------

    def transfers(self) -> list[Transfer]:
        with self._cond:
            items = [dataclasses.replace(t) for t in self._transfers.values()]
        return sorted(items, key=lambda t: t.updated, reverse=True)

    def _track(self, t: Transfer) -> None:
        with self._cond:
            self._transfers[t.id] = t
            if len(self._transfers) > MAX_HISTORY:
                oldest = sorted(self._transfers.values(), key=lambda x: x.updated)
                for old in oldest[: len(self._transfers) - MAX_HISTORY]:
                    if old.status in ("done", "failed"):
                        del self._transfers[old.id]

    def _update(self, t: Transfer, **changes) -> None:
        with self._cond:
            for k, v in changes.items():
                setattr(t, k, v)
            t.updated = time.time()
        if "status" in changes and self.on_event:
            self.on_event(dataclasses.replace(t))

    # ---- PC -> phone -----------------------------------------------------------

    def send_to_phone(self, paths: list[str | Path]) -> list[Transfer]:
        """Queue files for the phone. Hashing runs in the background, then they appear in
        the outbox the phone is long-polling."""
        queued = []
        for raw in paths:
            path = Path(raw)
            if not path.is_file():
                continue
            t = Transfer(id=uuid.uuid4().hex, direction="out", name=path.name,
                         size=path.stat().st_size, path=str(path))
            self._track(t)
            queued.append(t)
        if queued:
            threading.Thread(target=self._prepare, args=(queued,), daemon=True).start()
        return queued

    def _prepare(self, items: list[Transfer]) -> None:
        for t in items:
            try:
                digest = sha256_file(Path(t.path))
            except OSError as exc:
                self._update(t, status="failed", error=str(exc))
                continue
            with self._cond:
                t.sha256 = digest
                self._outbox[t.id] = t
                self._outbox_version += 1
                self._cond.notify_all()

    def wait_outbox(self, since: int, wait: float) -> tuple[int, list[Transfer]]:
        deadline = time.monotonic() + max(0.0, min(wait, MAX_WAIT_S))
        with self._cond:
            while self._outbox_version == since:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cond.wait(left)
            return self._outbox_version, [dataclasses.replace(t) for t in self._outbox.values()]

    def outbox_item(self, item_id: str) -> Transfer | None:
        with self._cond:
            return self._outbox.get(item_id)

    def acknowledge(self, item_id: str, device: str) -> bool:
        with self._cond:
            t = self._outbox.pop(item_id, None)
            if t is None:
                return False
            self._outbox_version += 1
            self._cond.notify_all()
        self._update(t, status="done", done=t.size, device=device)
        log.info("sent %s to %s", t.name, device)
        return True

    # ---- phone -> PC -----------------------------------------------------------

    def receive(self, name: str, length: int, stream, device: str) -> Transfer:
        self.files_dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(self.files_dir).free < length + FREE_SPACE_MARGIN:
            raise InsufficientStorage(f"not enough free space for {length} bytes")
        t = Transfer(id=uuid.uuid4().hex, direction="in", name=safe_filename(name), size=length,
                     status="transferring", device=device)
        self._track(t)
        part = self.files_dir / f".{t.id}.part"
        digest = hashlib.sha256()
        try:
            with open(part, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = stream.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ConnectionError("upload ended early")
                    f.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                    with self._cond:
                        t.done += len(chunk)
            with self._save_lock:
                final = unique_path(self.files_dir, t.name)
                os.replace(part, final)
        except BaseException as exc:
            part.unlink(missing_ok=True)
            self._update(t, status="failed", error=str(exc) or type(exc).__name__)
            raise
        self._update(t, status="done", name=final.name, path=str(final), sha256=digest.hexdigest())
        log.info("received %s from %s (%d bytes)", final.name, device, length)
        return t


# ---- HTTP plumbing ------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second receiver bind the same port and steal requests.
    # Elsewhere it only allows a quick restart while old connections sit in TIME_WAIT.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address, handler, service: FileService):
        self.service = service
        super().__init__(address, handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = "Airwire"
    timeout = 60
    server: _Server

    @property
    def service(self) -> FileService:
        return self.server.service

    def log_message(self, fmt, *args) -> None:
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---- helpers ---------------------------------------------------------------

    def _json(self, status: int, payload: dict | None = None) -> None:
        body = json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _device(self) -> str | None:
        """Paired device name for this request, or None after sending 401."""
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        device = self.service.pairing.device_for(token)
        if device is None:
            self._json(401, {"error": "not paired"})
        return device

    def _route(self) -> tuple[str, dict[str, list[str]]]:
        parts = urlsplit(self.path)
        return parts.path, parse_qs(parts.query)

    # ---- verbs -----------------------------------------------------------------

    def do_POST(self) -> None:
        path, _ = self._route()
        if path != f"{API}/pair":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(min(length, 4096)) or b"{}")
            token = self.service.pairing.pair(str(data.get("code", "")), str(data.get("device", "")))
        except PairingError as exc:
            return self._json(exc.status, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "bad request"})
        log.info("paired with %s", data.get("device"))
        self._json(200, {"token": token, "name": self.service.name})

    def do_GET(self) -> None:
        path, query = self._route()
        if path == f"{API}/hello":
            if (device := self._device()) is not None:
                self._json(200, {"name": self.service.name, "device": device})
            return
        if path == f"{API}/outbox":
            if self._device() is None:
                return
            try:
                since = int(query.get("since", ["-1"])[0])
                wait = float(query.get("wait", ["0"])[0])
            except ValueError:
                return self._json(400, {"error": "bad query"})
            version, items = self.service.wait_outbox(since, wait)
            return self._json(200, {"version": version, "files": [
                {"id": t.id, "name": t.name, "size": t.size, "sha256": t.sha256} for t in items]})
        if path.startswith(f"{API}/outbox/"):
            if (device := self._device()) is None:
                return
            return self._send_file(path.rsplit("/", 1)[-1], device)
        self._json(404, {"error": "not found"})

    def do_PUT(self) -> None:
        path, _ = self._route()
        if not path.startswith(f"{API}/files/"):
            return self._json(404, {"error": "not found"})
        if (device := self._device()) is None:
            return
        name = unquote(path[len(f"{API}/files/"):])
        length_header = self.headers.get("Content-Length")
        if length_header is None or not length_header.isdigit():
            return self._json(411, {"error": "Content-Length required"})
        try:
            t = self.service.receive(name, int(length_header), self.rfile, device)
        except InsufficientStorage as exc:
            return self._json(507, {"error": str(exc)})
        except (ConnectionError, OSError) as exc:
            log.warning("upload of %s from %s failed: %s", name, device, exc)
            return
        self._json(201, {"name": t.name, "size": t.size, "sha256": t.sha256})

    def do_DELETE(self) -> None:
        path, _ = self._route()
        if not path.startswith(f"{API}/outbox/"):
            return self._json(404, {"error": "not found"})
        if (device := self._device()) is None:
            return
        if self.service.acknowledge(path.rsplit("/", 1)[-1], device):
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json(404, {"error": "unknown file"})

    def _send_file(self, item_id: str, device: str) -> None:
        svc = self.service
        t = svc.outbox_item(item_id)
        if t is None:
            return self._json(404, {"error": "unknown file"})
        try:
            f = open(t.path, "rb")
        except OSError as exc:
            svc._update(t, status="failed", error=str(exc))
            return self._json(410, {"error": "file is no longer available"})
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(t.size))
            self.send_header("X-Airwire-Name", quote(t.name))
            self.send_header("X-Airwire-Sha256", t.sha256)
            self.end_headers()
            svc._update(t, status="transferring", done=0, device=device)
            try:
                while chunk := f.read(CHUNK):
                    self.wfile.write(chunk)
                    with svc._cond:
                        t.done += len(chunk)
            except OSError as exc:
                # Stays in the outbox; the phone retries on its next poll.
                svc._update(t, status="queued", error=str(exc))
