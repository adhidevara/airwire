"""Captures what this PC is playing (system audio loopback) for PC -> phone streaming.

Uses the `soundcard` package: WASAPI loopback on Windows, the sink monitor on Linux
(PulseAudio or PipeWire). macOS has no loopback without a virtual device such as BlackHole.

The capture follows the default output device: when it changes (headphones plugged in, a
Bluetooth headset connecting) or the device disappears (Windows reports
AUDCLNT_E_DEVICE_INVALIDATED), the loopback is reopened on the current default.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import warnings
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHANNELS = 2
FRAME = 480  # 10 ms

FrameFn = Callable[[np.ndarray, int], None]


class CaptureError(RuntimeError):
    pass


def _soundcard():
    try:
        import soundcard as sc
    except ImportError as exc:
        raise CaptureError("PC audio capture needs the `soundcard` package "
                           "(pip install soundcard)") from exc
    except Exception as exc:  # noqa: BLE001 - backends fail in different ways
        raise CaptureError(f"PC audio capture is unavailable: {exc}") from exc
    if sys.platform == "darwin":
        raise CaptureError("macOS can't capture system audio without a virtual device "
                           "such as BlackHole")
    return sc


def default_speaker_id() -> str | None:
    try:
        return str(_soundcard().default_speaker().id)
    except Exception:  # noqa: BLE001 - e.g. no output device at all right now
        return None


def open_loopback() -> tuple[Any, str]:
    """Loopback "microphone" of the default speaker, and that speaker's id."""
    sc = _soundcard()
    try:
        speaker = sc.default_speaker()
        # WASAPI loopback shares the speaker's id; a PulseAudio monitor is found by name.
        key = speaker.id if sys.platform == "win32" else speaker.name
        return sc.get_microphone(id=str(key), include_loopback=True), str(speaker.id)
    except Exception as exc:  # noqa: BLE001
        raise CaptureError(f"no loopback device for the default speaker: {exc}") from exc


def to_int16(block: np.ndarray) -> np.ndarray:
    """soundcard's float32 (-1..1) -> int16 stereo, upmixing mono."""
    if block.ndim == 1:
        block = block[:, None]
    if block.shape[1] == 1:
        block = np.repeat(block, CHANNELS, axis=1)
    elif block.shape[1] > CHANNELS:
        block = block[:, :CHANNELS]
    return np.clip(block * 32767.0, -32768, 32767).astype(np.int16)


class LoopbackCapture:
    """Calls `on_frame(pcm, capture_ms)` with 10 ms of int16 stereo from a capture thread.

    `on_error` is called only when the capture can't be reopened (MAX_FAILURES in a row).
    """

    MAX_FAILURES = 5
    RETRY_S = 0.5
    CHECK_DEFAULT_EVERY = 200   # frames (2 s) between checks of the default output device
    STABLE_S = 2.0              # a stream that ran this long resets the failure count

    def __init__(self, on_frame: FrameFn, *, on_error: Callable[[str], None] | None = None,
                 opener: Callable[[], tuple[Any, str]] = open_loopback,
                 default_id: Callable[[], str | None] = default_speaker_id):
        self._on_frame = on_frame
        self._on_error = on_error
        self._open = opener
        self._default_id = default_id
        self._running = False
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self.restarts = 0

    def start(self) -> None:
        """Opens the loopback device; raises CaptureError if that fails."""
        mic, device = self._open()
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(mic, device), name="pc-capture",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self._error:
            self._running = False
            raise CaptureError(self._error)

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)

    def _run(self, mic, device: str) -> None:
        # WASAPI flags a discontinuity at start-up and after the PC was silent; harmless here.
        warnings.filterwarnings("ignore", message="data discontinuity")
        failures = 0
        while self._running:
            opened_at = time.monotonic()
            try:
                self._record(mic, device)
            except Exception as exc:  # noqa: BLE001 - device unplugged, format changed, ...
                if not self._ready.is_set():
                    self._error = f"capture failed: {exc}"
                    log.exception("loopback capture failed")
                    self._ready.set()
                    return
                if time.monotonic() - opened_at >= self.STABLE_S:
                    failures = 0
                failures += 1
                log.warning("loopback capture interrupted (%s); reopening", exc)
                if failures >= self.MAX_FAILURES:
                    self._fail(f"capture failed: {exc}")
                    return
                time.sleep(self.RETRY_S)
            if not self._running:
                return
            try:
                mic, device = self._open()
                self.restarts += 1
            except CaptureError as exc:
                failures += 1
                if failures >= self.MAX_FAILURES:
                    self._fail(str(exc))
                    return
                time.sleep(self.RETRY_S)

    def _record(self, mic, device: str) -> None:
        """Captures until stopped or until the default output device changes."""
        with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=FRAME) as rec:
            self._ready.set()
            pending = np.zeros((0, CHANNELS), np.int16)
            frames = 0
            while self._running:
                block = to_int16(rec.record(numframes=FRAME))
                pending = np.concatenate([pending, block]) if len(pending) else block
                while len(pending) >= FRAME:
                    self._on_frame(pending[:FRAME], 0)
                    pending = pending[FRAME:]
                    frames += 1
                if frames >= self.CHECK_DEFAULT_EVERY:
                    frames = 0
                    current = self._default_id()
                    if current is not None and current != device:
                        log.info("default output device changed; following it")
                        return

    def _fail(self, message: str) -> None:
        self._error = message
        log.error("loopback capture stopped: %s", message)
        if self._running and self._on_error:
            self._running = False
            self._on_error(message)
