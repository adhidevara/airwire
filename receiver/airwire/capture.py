"""Captures what this PC is playing (system audio loopback) for PC -> phone streaming.

Uses the `soundcard` package: WASAPI loopback on Windows, the sink monitor on Linux
(PulseAudio or PipeWire). macOS has no loopback without a virtual device such as BlackHole.
"""

from __future__ import annotations

import logging
import sys
import threading
import warnings
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHANNELS = 2
FRAME = 480  # 10 ms

FrameFn = Callable[[np.ndarray, int], None]


class CaptureError(RuntimeError):
    pass


def _loopback_microphone():
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
    try:
        speaker = sc.default_speaker()
        return sc.get_microphone(id=str(speaker.name), include_loopback=True)
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
    """Calls `on_frame(pcm, capture_ms)` with 10 ms of int16 stereo from a capture thread."""

    def __init__(self, on_frame: FrameFn, *, on_error: Callable[[str], None] | None = None):
        self._on_frame = on_frame
        self._on_error = on_error
        self._running = False
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None

    def start(self) -> None:
        """Opens the loopback device; raises CaptureError if that fails."""
        mic = _loopback_microphone()
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(mic,), name="pc-capture",
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

    def _run(self, mic) -> None:
        # WASAPI flags a discontinuity at start-up and after the PC was silent; harmless here.
        warnings.filterwarnings("ignore", message="data discontinuity")
        try:
            # Windows needs COM on this thread for WASAPI; soundcard handles it per thread.
            with mic.recorder(samplerate=SAMPLE_RATE, channels=CHANNELS, blocksize=FRAME) as rec:
                self._ready.set()
                pending = np.zeros((0, CHANNELS), np.int16)
                while self._running:
                    block = to_int16(rec.record(numframes=FRAME))
                    pending = np.concatenate([pending, block]) if len(pending) else block
                    while len(pending) >= FRAME:
                        self._on_frame(pending[:FRAME], 0)
                        pending = pending[FRAME:]
        except Exception as exc:  # noqa: BLE001
            self._error = f"capture failed: {exc}"
            log.exception("loopback capture failed")
            self._ready.set()
            if self._running and self._on_error:
                self._running = False
                self._on_error(self._error)
