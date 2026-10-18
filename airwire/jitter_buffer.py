"""Adaptive jitter buffer.

Producer: the UDP thread calls `push()` for every AUDIO packet.
Consumer: the audio callback calls `pop()` once per frame it needs.

Behaviour:
- Pre-roll: nothing plays until `target` frames are buffered.
- Missing frame at play-out time: redundant copy, then Opus in-band FEC from the
  next packet, then Opus packet-loss concealment (PLC).
- Underrun (nothing buffered at all): play one PLC frame without advancing, which
  delays the timeline by one frame, and raise `target`. After `STALL_FRAMES`
  consecutive underruns the sender is considered stalled: go silent and pre-roll again.
- Latency control: if the buffer never dropped to `target` during a whole window,
  merge two frames into one with a crossfade. This removes surplus latency and
  compensates clock drift between the phone and the sound card.
  `target` shrinks by one after `shrink_after_s` without an underrun.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .opus import OpusDecoder, OpusError


@dataclass
class JitterStats:
    played: int = 0
    recovered: int = 0      # rebuilt from the redundant copy in the next packet
    fec: int = 0            # rebuilt via Opus in-band FEC (libopus falls back to PLC)
    concealed: int = 0      # Opus PLC
    underruns: int = 0
    late: int = 0
    duplicates: int = 0
    dropped: int = 0        # frames removed to cut latency / compensate drift
    resets: int = 0


class JitterBuffer:
    STALL_FRAMES = 5

    def __init__(self, decoder_factory: Callable[[], OpusDecoder] = OpusDecoder, *,
                 initial_depth: int = 2, min_depth: int = 1, max_depth: int = 10,
                 shrink_after_s: float = 10.0, drift_window: int = 200,
                 clock: Callable[[], float] = time.monotonic):
        self._decoder = decoder_factory()
        self.sample_rate = self._decoder.sample_rate
        self.min_depth = min_depth
        self.max_depth = max_depth
        self.target = initial_depth
        self.shrink_after_s = shrink_after_s
        self.drift_window = drift_window
        self._clock = clock
        self._lock = threading.Lock()
        self._frame_samples = self.sample_rate // 100  # until the first packet says otherwise
        self._fades: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.stats = JitterStats()
        self._avg_depth = 0.0
        self._clear()

    # ---- state ---------------------------------------------------------------

    def _clear(self) -> None:
        self._packets: dict[int, bytes] = {}
        self._recovered: dict[int, bytes] = {}
        self._next: int | None = None
        self._highest: int | None = None
        self._playing = False
        self._stall = 0
        self._stable_since = self._clock()
        self._window_min: int | None = None
        self._window_count = 0

    def reset(self) -> None:
        """Forget everything buffered (new session / sender gone). Keeps `target`."""
        with self._lock:
            self._clear()
            self._decoder.reset()
            self._avg_depth = 0.0
            self.stats.resets += 1

    def _depth(self) -> int:
        if self._next is None or self._highest is None or self._highest < self._next:
            return 0
        return self._highest - self._next + 1

    @property
    def frame_ms(self) -> float:
        return self._frame_samples * 1000 / self.sample_rate

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def depth(self) -> int:
        with self._lock:
            return self._depth()

    @property
    def buffer_ms(self) -> float:
        """Smoothed buffered audio at play-out time."""
        return self._avg_depth * self.frame_ms

    @property
    def target_ms(self) -> float:
        return self.target * self.frame_ms

    # ---- producer ------------------------------------------------------------

    def push(self, seq: int, primary: bytes, redundant: bytes | None = None) -> None:
        with self._lock:
            if self._next is None:
                self._next = seq
            elif seq < self._next:
                if self._playing:
                    self.stats.late += 1
                    return
                self._next = seq  # out-of-order during pre-roll: start earlier
            if seq in self._packets:
                self.stats.duplicates += 1
                return
            self._packets[seq] = primary
            self._recovered.pop(seq, None)
            prev = seq - 1
            if redundant and prev >= self._next and prev not in self._packets:
                self._recovered.setdefault(prev, redundant)
            if self._highest is None or seq > self._highest:
                self._highest = seq

    # ---- consumer ------------------------------------------------------------

    def pop(self) -> np.ndarray | None:
        """Next frame of int16 PCM shaped (samples, channels), or None for silence."""
        with self._lock:
            if self._next is None:
                return None
            depth = self._depth()
            if not self._playing:
                if depth < self.target:
                    return None
                self._playing = True
                self._stable_since = self._clock()
            if depth == 0:
                return self._underrun()

            self._stall = 0
            self._avg_depth += (depth - self._avg_depth) / 32
            depth = self._catch_up(depth)
            self._maybe_shrink()

            frame = self._take_next()
            if self._drift_check(depth) and self._depth() > 0:
                frame = self._crossfade(frame, self._take_next())
                self.stats.dropped += 1
            return frame

    def _underrun(self) -> np.ndarray | None:
        self.stats.underruns += 1
        self._stall += 1
        if self._stall == 1:
            self.target = min(self.target + 1, self.max_depth)
        self._stable_since = self._clock()
        if self._stall > self.STALL_FRAMES:
            self._clear()
            self._decoder.reset()
            self._avg_depth = 0.0
            return None
        self.stats.concealed += 1
        return self._decoder.decode(None, self._frame_samples)

    def _catch_up(self, depth: int) -> int:
        """After a burst (e.g. Wi-Fi hiccup) skip straight to `target` frames behind."""
        if depth <= self.max_depth * 2:
            return depth
        new_next = self._highest - self.target + 1
        self.stats.dropped += new_next - self._next
        self._next = new_next
        self._packets = {s: p for s, p in self._packets.items() if s >= new_next}
        self._recovered = {s: p for s, p in self._recovered.items() if s >= new_next}
        self._window_min, self._window_count = None, 0
        return self._depth()

    def _maybe_shrink(self) -> None:
        now = self._clock()
        if self.target > self.min_depth and now - self._stable_since >= self.shrink_after_s:
            self.target -= 1
            self._stable_since = now

    def _drift_check(self, depth: int) -> bool:
        """True once per window if the buffer stayed above target the whole window."""
        self._window_count += 1
        self._window_min = depth if self._window_min is None else min(self._window_min, depth)
        if self._window_count < self.drift_window:
            return False
        surplus = self._window_min > self.target
        self._window_min, self._window_count = None, 0
        return surplus

    def _take_next(self) -> np.ndarray:
        seq = self._next
        self._next = seq + 1
        self.stats.played += 1
        packet = self._packets.pop(seq, None)
        redundant = self._recovered.pop(seq, None)
        if packet is not None:
            return self._decode(packet)
        if redundant is not None:
            self.stats.recovered += 1
            return self._decode(redundant)
        following = self._packets.get(seq + 1)
        if following is not None:
            self.stats.fec += 1
            return self._decoder.decode(following, self._frame_samples, fec=True)
        self.stats.concealed += 1
        return self._decoder.decode(None, self._frame_samples)

    def _decode(self, packet: bytes) -> np.ndarray:
        try:
            pcm = self._decoder.decode(packet)
        except OpusError:
            self.stats.concealed += 1
            return self._decoder.decode(None, self._frame_samples)
        if len(pcm):
            self._frame_samples = len(pcm)
        return pcm

    def _crossfade(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        n = min(len(a), len(b))
        if n == 0:
            return b
        if n not in self._fades:
            up = np.linspace(0.0, 1.0, n, dtype=np.float32)[:, None]
            self._fades[n] = (1.0 - up, up)
        fade_out, fade_in = self._fades[n]
        mixed = a[:n].astype(np.float32) * fade_out + b[:n].astype(np.float32) * fade_in
        return np.clip(mixed, -32768, 32767).astype(np.int16)
