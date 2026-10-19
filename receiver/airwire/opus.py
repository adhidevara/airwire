"""Minimal ctypes binding to libopus.

Written by hand (instead of using a wrapper package) because the receiver needs
packet-loss concealment (`opus_decode(NULL)`) and in-band FEC (`decode_fec=1`).

Library lookup order:
1. `OPUS_LIBRARY` environment variable (full path)
2. `receiver/lib/opus.dll` (drop-in)
3. the `opus.dll` bundled with the `pyogg` wheel on Windows
4. the system library (`ctypes.util.find_library("opus")`), and on Linux the usual
   library directories directly, in case the ldconfig cache is stale
"""

from __future__ import annotations

import ctypes
import ctypes.util
import importlib.util
import os
import sys
from pathlib import Path

import numpy as np

OPUS_OK = 0
OPUS_APPLICATION_VOIP = 2048
OPUS_APPLICATION_AUDIO = 2049
OPUS_APPLICATION_RESTRICTED_LOWDELAY = 2051

_SET_BITRATE = 4002
_SET_COMPLEXITY = 4010
_SET_INBAND_FEC = 4012
_SET_PACKET_LOSS_PERC = 4014
_GET_LOOKAHEAD = 4027
_RESET_STATE = 4028

MAX_FRAME_MS = 120
MAX_PACKET_BYTES = 1500

_LIB_NAMES = {"win32": "opus.dll", "darwin": "libopus.dylib"}


class OpusError(RuntimeError):
    pass


def _candidates() -> list[str]:
    paths: list[str] = []
    if env := os.environ.get("OPUS_LIBRARY"):
        paths.append(env)
    lib_name = _LIB_NAMES.get(sys.platform, "libopus.so.0")
    paths.append(str(Path(__file__).resolve().parent.parent / "lib" / lib_name))
    # If packaged via PyInstaller (.app bundle)
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        paths.append(os.path.join(sys._MEIPASS, lib_name))
        paths.append(os.path.join(sys._MEIPASS, "Frameworks", lib_name))
    # Homebrew / macOS library paths
    if sys.platform == "darwin":
        paths.append(f"/usr/local/lib/{lib_name}")
        paths.append(f"/usr/local/opt/opus/lib/{lib_name}")
        paths.append(f"/opt/homebrew/lib/{lib_name}")
        paths.append(f"/opt/homebrew/opt/opus/lib/{lib_name}")
    spec = importlib.util.find_spec("pyogg")
    if spec and spec.submodule_search_locations:
        for loc in spec.submodule_search_locations:
            paths.append(os.path.join(loc, lib_name))
    if found := ctypes.util.find_library("opus"):
        paths.append(found)
    if sys.platform.startswith("linux"):
        # find_library reads the ldconfig cache; open the file directly if that cache is stale.
        machine = os.uname().machine
        for libdir in (f"/usr/lib/{machine}-linux-gnu", "/usr/lib64", "/usr/lib", "/usr/local/lib"):
            paths.append(f"{libdir}/libopus.so.0")
    return paths


_lib: ctypes.CDLL | None = None


def load_libopus() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    errors = []
    for path in _candidates():
        if os.path.isabs(path) and not os.path.exists(path):
            continue
        try:
            lib = ctypes.CDLL(path)
            break
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    else:
        if sys.platform == "darwin":
            hint = "Install `brew install opus` or place libopus.dylib in receiver/lib/"
        elif sys.platform == "win32":
            hint = "Install `pyogg` (bundles opus.dll on Windows) or place opus.dll in receiver/lib/"
        else:
            hint = "Install `sudo apt install libopus0` (then `sudo ldconfig`)"
        raise OpusError(
            f"libopus not found. {hint}, or set OPUS_LIBRARY. Tried: " + "; ".join(errors)
        )

    c_int, c_void_p, c_char_p = ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p
    pcm_p = ctypes.POINTER(ctypes.c_int16)
    lib.opus_decoder_create.argtypes = [ctypes.c_int32, c_int, ctypes.POINTER(c_int)]
    lib.opus_decoder_create.restype = c_void_p
    lib.opus_decode.argtypes = [c_void_p, c_char_p, ctypes.c_int32, pcm_p, c_int, c_int]
    lib.opus_decode.restype = c_int
    lib.opus_decoder_destroy.argtypes = [c_void_p]
    lib.opus_encoder_create.argtypes = [ctypes.c_int32, c_int, c_int, ctypes.POINTER(c_int)]
    lib.opus_encoder_create.restype = c_void_p
    lib.opus_encode.argtypes = [c_void_p, pcm_p, c_int, c_char_p, ctypes.c_int32]
    lib.opus_encode.restype = ctypes.c_int32
    lib.opus_encoder_destroy.argtypes = [c_void_p]
    lib.opus_strerror.argtypes = [c_int]
    lib.opus_strerror.restype = c_char_p
    lib.opus_get_version_string.restype = c_char_p
    # opus_*_ctl are variadic. Declaring only the fixed (state, request) parameters makes
    # ctypes pass the value as a variadic argument, which Apple Silicon passes on the stack.
    for ctl in (lib.opus_encoder_ctl, lib.opus_decoder_ctl):
        ctl.argtypes = [c_void_p, c_int]
        ctl.restype = c_int
    _lib = lib
    return lib


def version() -> str:
    return load_libopus().opus_get_version_string().decode()


def _check(code: int, what: str) -> int:
    if code < 0:
        msg = load_libopus().opus_strerror(code).decode()
        raise OpusError(f"{what} failed: {msg} ({code})")
    return code


def _pcm_ptr(arr: np.ndarray):
    return arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int16))


class OpusDecoder:
    """Decodes Opus packets to interleaved int16 PCM shaped (samples, channels)."""

    def __init__(self, sample_rate: int = 48000, channels: int = 2):
        self._lib = load_libopus()
        self.sample_rate = sample_rate
        self.channels = channels
        err = ctypes.c_int()
        self._st = self._lib.opus_decoder_create(sample_rate, channels, ctypes.byref(err))
        _check(err.value, "opus_decoder_create")
        self._max_samples = sample_rate * MAX_FRAME_MS // 1000

    def decode(self, packet: bytes | None, frame_samples: int | None = None,
               fec: bool = False) -> np.ndarray:
        """Decode `packet`.

        `packet=None` runs packet-loss concealment for `frame_samples`.
        `fec=True` recovers the frame *before* `packet` from its in-band FEC data
        (falls back to concealment inside libopus when the packet has none);
        `frame_samples` must then be the duration of the lost frame.
        """
        if packet is None or fec:
            if not frame_samples:
                raise ValueError("frame_samples is required for PLC/FEC")
            size = frame_samples
        else:
            size = self._max_samples
        out = np.empty((size, self.channels), dtype=np.int16)
        n = self._lib.opus_decode(
            self._st, packet, len(packet) if packet else 0, _pcm_ptr(out), size, int(fec)
        )
        _check(n, "opus_decode")
        return out[:n]

    def reset(self) -> None:
        self._lib.opus_decoder_ctl(ctypes.c_void_p(self._st), ctypes.c_int(_RESET_STATE))

    def close(self) -> None:
        if getattr(self, "_st", None):
            self._lib.opus_decoder_destroy(self._st)
            self._st = None

    def __del__(self):
        self.close()


class OpusEncoder:
    """Encoder used by the fake sender and tests (the Android app has its own, via JNI)."""

    def __init__(self, sample_rate: int = 48000, channels: int = 2,
                 application: int = OPUS_APPLICATION_AUDIO, bitrate: int = 128_000,
                 complexity: int = 8, inband_fec: bool = True, loss_perc: int = 10):
        self._lib = load_libopus()
        self.sample_rate = sample_rate
        self.channels = channels
        err = ctypes.c_int()
        self._st = self._lib.opus_encoder_create(sample_rate, channels, application,
                                                 ctypes.byref(err))
        _check(err.value, "opus_encoder_create")
        self._out = ctypes.create_string_buffer(MAX_PACKET_BYTES)
        self._ctl(_SET_BITRATE, bitrate)
        self._ctl(_SET_COMPLEXITY, complexity)
        self._ctl(_SET_INBAND_FEC, int(inband_fec))
        self._ctl(_SET_PACKET_LOSS_PERC, loss_perc)

    def _ctl(self, request: int, value: int) -> None:
        code = self._lib.opus_encoder_ctl(ctypes.c_void_p(self._st), ctypes.c_int(request),
                                          ctypes.c_int(value))
        _check(code, f"opus_encoder_ctl({request})")

    def set_bitrate(self, bitrate: int) -> None:
        self._ctl(_SET_BITRATE, bitrate)

    def encode(self, pcm: np.ndarray) -> bytes:
        pcm = np.ascontiguousarray(pcm, dtype=np.int16)
        frame = pcm.shape[0]
        if pcm.ndim != 2 or pcm.shape[1] != self.channels:
            raise ValueError(f"expected (n, {self.channels}) int16 PCM, got {pcm.shape}")
        n = self._lib.opus_encode(self._st, _pcm_ptr(pcm), frame, self._out, MAX_PACKET_BYTES)
        _check(n, "opus_encode")
        return self._out.raw[:n]

    def close(self) -> None:
        if getattr(self, "_st", None):
            self._lib.opus_encoder_destroy(self._st)
            self._st = None

    def __del__(self):
        self.close()
