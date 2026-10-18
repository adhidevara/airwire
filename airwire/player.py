"""Low-latency playback through PortAudio (WASAPI shared mode on Windows)."""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 48_000
CHANNELS = 2
BLOCK = 480  # 10 ms


@dataclass(frozen=True)
class OutputDevice:
    index: int
    name: str
    hostapi: str
    is_default: bool


def _hostapi_index(name_part: str) -> int | None:
    for i, api in enumerate(sd.query_hostapis()):
        if name_part.lower() in api["name"].lower():
            return i
    return None


def _preferred_hostapi() -> int:
    if sys.platform == "win32":
        wasapi = _hostapi_index("WASAPI")
        if wasapi is not None:
            return wasapi
    return sd.default.hostapi


def list_output_devices() -> list[OutputDevice]:
    """Output devices of the preferred host API (WASAPI on Windows), so names aren't repeated."""
    api_index = _preferred_hostapi()
    api = sd.query_hostapis(api_index)
    devices = []
    for idx, dev in enumerate(sd.query_devices()):
        if dev["hostapi"] == api_index and dev["max_output_channels"] > 0:
            devices.append(OutputDevice(idx, dev["name"], api["name"],
                                        idx == api["default_output_device"]))
    return devices


def default_output_device() -> int | None:
    idx = sd.query_hostapis(_preferred_hostapi())["default_output_device"]
    return idx if idx >= 0 else None


def find_device(query: str | int | None) -> int | None:
    """Resolve an index or a case-insensitive name fragment to a device index."""
    if query is None or query == "":
        return None
    devices = list_output_devices()
    if isinstance(query, int) or str(query).isdigit():
        idx = int(query)
        return idx if any(d.index == idx for d in devices) else None
    q = str(query).lower()
    for d in devices:
        if q in d.name.lower():
            return d.index
    return None


class AudioPlayer:
    """Pulls frames from `source()` inside the PortAudio callback.

    `source` returns int16 PCM shaped (n, CHANNELS) or None for silence. Frames may be
    any length; leftovers are kept for the next callback.
    """

    def __init__(self, source: Callable[[], np.ndarray | None]):
        self._source = source
        self._pending = np.zeros((0, CHANNELS), dtype=np.int16)
        self._gain = 1.0
        self._volume = 1.0
        self._stream: sd.OutputStream | None = None
        self._lock = threading.Lock()
        self.device: int | None = None
        self.host_api = ""
        self.underflows = 0
        self.error: str | None = None

    @property
    def volume(self) -> float:
        return self._volume

    @volume.setter
    def volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, float(value)))
        self._gain = self._volume ** 2  # perceptual curve: 50% on the slider ≈ -12 dB

    @property
    def latency_ms(self) -> float:
        stream = self._stream
        return stream.latency * 1000 if stream is not None else 0.0

    @property
    def running(self) -> bool:
        return self._stream is not None and self._stream.active

    def start(self, device: int | None = None) -> None:
        with self._lock:
            self._close()
            device = default_output_device() if device is None else device
            self.error = None
            try:
                self._stream = self._open(device)
            except Exception as exc:  # noqa: BLE001 - PortAudio raises several types
                log.warning("opening device %s failed (%s), falling back to system default",
                            device, exc)
                try:
                    device = None
                    self._stream = self._open(None)
                except Exception as exc2:  # noqa: BLE001
                    self.error = str(exc2)
                    log.error("no usable output device: %s", exc2)
                    return
            self.device = device
            self.host_api = sd.query_hostapis(
                sd.query_devices(self._stream.device)["hostapi"])["name"]
            self._stream.start()
            log.info("playing on %s (%s), latency %.1f ms",
                     sd.query_devices(self._stream.device)["name"], self.host_api,
                     self.latency_ms)

    def stop(self) -> None:
        with self._lock:
            self._close()

    def _close(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._pending = np.zeros((0, CHANNELS), dtype=np.int16)

    def _open(self, device: int | None) -> sd.OutputStream:
        extra = None
        if device is not None and sys.platform == "win32":
            api = sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"]
            if "WASAPI" in api:
                # Shared mode; let Windows resample when the mix format isn't 48 kHz.
                extra = sd.WasapiSettings(auto_convert=True)
        return sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16", blocksize=BLOCK,
            latency="low", device=device, callback=self._callback, extra_settings=extra,
        )

    def _callback(self, outdata: np.ndarray, frames: int, _time, status) -> None:
        if status.output_underflow:
            self.underflows += 1
        filled = 0
        pending = self._pending
        while filled < frames:
            if len(pending) == 0:
                chunk = self._source()
                if chunk is None or len(chunk) == 0:
                    outdata[filled:] = 0
                    break
                pending = chunk
            n = min(frames - filled, len(pending))
            outdata[filled:filled + n] = pending[:n]
            pending = pending[n:]
            filled += n
        self._pending = pending
        if self._gain != 1.0:
            np.multiply(outdata, self._gain, out=outdata, casting="unsafe")
