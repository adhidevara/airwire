"""Adaptive jitter buffer.

Producer: the UDP thread calls `push()` for every AUDIO packet.
Consumer: the audio callback calls `pop()` once per frame it needs.

Behaviour:
- Pre-roll: nothing plays until `target` frames are buffered.
- Missing frame at play-out time: redundant copy, then Opus in-band FEC from the
  next packet, then Opus packet-loss concealment (PLC).
- Underrun (nothing buffered at all): play one PLC frame without advancing, which
  delays the timeline by one frame. After `STALL_FRAMES` consecutive underruns the
  sender is considered stalled: go silent and pre-roll again.
- Latency control: every played frame records its *slack*, how long its data sat in
  the buffer before it was needed. A frame that arrived after it was needed records a
  negative slack. If every frame of the last `window` (10 s) waited longer than one
  frame plus `margin_ms`, that frame of latency is not needed: two frames are merged
  into one with a crossfade. A late or nearly late frame holds the latency for a whole
  window, so it is only cut back once the network has been calm that long. Merging
  also compensates clock drift between the phone and the sound card.
  `late_quantile` > 0 tolerates that fraction of late frames instead.
"""

from __future__ import annotations

import bisect
import math
import threading
import time
from collections import deque
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
    MIN_SAMPLES = 100          # slack samples needed before latency is cut
    MAX_LATE_WEIGHT = 20       # cap on samples recorded for one late arrival

    def __init__(self, decoder_factory: Callable[[], OpusDecoder] = OpusDecoder, *,
                 initial_depth: int = 2, min_depth: int = 1, max_depth: int = 15,
                 window: int = 1000, late_quantile: float = 0.0, margin_ms: float = 2.0,
                 clock: Callable[[], float] = time.monotonic):
        self._decoder = decoder_factory()
        self.sample_rate = self._decoder.sample_rate
        self.min_depth = min_depth
        self.max_depth = max_depth
        self.target = initial_depth
        self.window = window
        self.late_quantile = late_quantile
        self.margin = margin_ms / 1000
        self._clock = clock
        self._lock = threading.Lock()
        self._frame_samples = self.sample_rate // 100  # until the first packet says otherwise
        self._fades: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        self.stats = JitterStats()
        self._avg_depth = 0.0
        self._clear()
        self._clear_slack()

    # ---- state ---------------------------------------------------------------

    def _clear(self) -> None:
        self._packets: dict[int, bytes] = {}
        self._recovered: dict[int, bytes] = {}
        self._avail: dict[int, float] = {}       # seq -> when its data (or a copy) arrived
        self._missed: dict[int, tuple[float, float]] = {}  # concealed seq -> (time, offset)
        self._next: int | None = None
        self._highest: int | None = None
        self._playing = False
        self._stall = 0
        self._waiting: tuple[float, float] | None = None   # underrun start (time, offset)

    def _clear_slack(self) -> None:
        # Samples are stored as `slack + offset`. Cutting a frame raises `offset` by one
        # frame and an underrun lowers it, which shifts every stored sample at once.
        self._slack_fifo: deque[float] = deque()
        self._slack_sorted: list[float] = []
        self._offset = 0.0
        self._since_drop = 0
        self._avg_wait = 0.0

    def reset(self, keep_history: bool = False) -> None:
        """Forget everything buffered (new session / sender gone). Keeps `target`.

        `keep_history` keeps the slack statistics too, for a sender that went quiet and
        comes back with the same session: the Wi-Fi it uses hasn't changed.
        """
        with self._lock:
            self._clear()
            if not keep_history:
                self._clear_slack()
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
    def _frame_s(self) -> float:
        return self._frame_samples / self.sample_rate

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

    @property
    def wait_ms(self) -> float:
        """Smoothed time a frame waits in the buffer before it is played."""
        return self._avg_wait * 1000

    # ---- slack statistics ----------------------------------------------------

    def _record(self, slack: float, offset: float | None = None, weight: int = 1) -> None:
        value = slack + (self._offset if offset is None else offset)
        for _ in range(weight):
            self._slack_fifo.append(value)
            bisect.insort(self._slack_sorted, value)
            if len(self._slack_fifo) > self.window:
                old = self._slack_fifo.popleft()
                del self._slack_sorted[bisect.bisect_left(self._slack_sorted, old)]

    def _record_late(self, lateness: float, offset: float) -> None:
        weight = min(self.MAX_LATE_WEIGHT, 1 + math.ceil(lateness / self._frame_s))
        self._record(-lateness, offset, weight)

    def _low_slack(self) -> float | None:
        """Lowest slack in the window, ignoring the `late_quantile` worst frames."""
        n = len(self._slack_sorted)
        if n < self.MIN_SAMPLES:
            return None
        return self._slack_sorted[int(n * self.late_quantile)] - self._offset

    # ---- producer ------------------------------------------------------------

    def push(self, seq: int, primary: bytes, redundant: bytes | None = None) -> None:
        now = self._clock()
        with self._lock:
            if self._next is None:
                self._next = seq
            elif seq < self._next:
                if self._playing:
                    self.stats.late += 1
                    missed = self._missed.pop(seq, None)
                    if missed is not None:
                        self._record_late(now - missed[0], missed[1])
                    return
                self._next = seq  # out-of-order during pre-roll: start earlier
            if seq in self._packets:
                self.stats.duplicates += 1
                return
            if self._waiting is not None:
                since, offset = self._waiting
                self._record_late(now - since, offset)
                self._waiting = None
            self._packets[seq] = primary
            self._recovered.pop(seq, None)
            self._avail.setdefault(seq, now)
            prev = seq - 1
            if redundant and prev >= self._next and prev not in self._packets:
                self._recovered.setdefault(prev, redundant)
                self._avail.setdefault(prev, now)
            if self._highest is None or seq > self._highest:
                self._highest = seq

    # ---- consumer ------------------------------------------------------------

    def pop(self) -> np.ndarray | None:
        """Next frame of int16 PCM shaped (samples, channels), or None for silence."""
        now = self._clock()
        with self._lock:
            if self._next is None:
                return None
            depth = self._depth()
            if not self._playing:
                if depth < self.target:
                    return None
                self._playing = True
            if depth == 0:
                return self._underrun(now)

            self._stall = 0
            self._avg_depth += (depth - self._avg_depth) / 32
            self._catch_up(depth)

            frame = self._take_next(now)
            self._since_drop += 1
            low = self._low_slack()
            if low is not None:
                self._update_target(low)
                if self._should_drop(low) and self._depth() > 0:
                    # Shift first: the merged-in frame's slack already reflects the cut.
                    self._offset += self._frame_s
                    frame = self._crossfade(frame, self._take_next(now))
                    self.stats.dropped += 1
                    self._since_drop = 0
            return frame

    def _underrun(self, now: float) -> np.ndarray | None:
        self.stats.underruns += 1
        self._stall += 1
        if self._stall == 1:
            self._waiting = (now, self._offset)
        if self._stall > self.STALL_FRAMES:
            # Pre-roll again at `target`; undo the concealment shifts and remember the stall.
            since, offset = self._waiting or (now, self._offset)
            self._offset = offset
            self._record_late(now - since, offset)
            self._clear()
            self._decoder.reset()
            self._avg_depth = 0.0
            return None
        self.stats.concealed += 1
        self._offset -= self._frame_s  # the timeline just moved one frame later
        return self._decoder.decode(None, self._frame_samples)

    def _catch_up(self, depth: int) -> None:
        """After a burst (e.g. Wi-Fi hiccup) skip straight to `target` frames behind."""
        if depth <= self.max_depth * 2:
            return
        new_next = self._highest - self.target + 1
        self.stats.dropped += new_next - self._next
        self._offset += (new_next - self._next) * self._frame_s
        self._next = new_next
        self._packets = {s: p for s, p in self._packets.items() if s >= new_next}
        self._recovered = {s: p for s, p in self._recovered.items() if s >= new_next}
        self._avail = {s: t for s, t in self._avail.items() if s >= new_next}

    def _update_target(self, low: float) -> None:
        """Depth that would leave `margin` of slack: used for pre-roll and shown in the UI."""
        surplus_frames = (low - self.margin) / self._frame_s
        ideal = round(self._avg_depth - surplus_frames)
        self.target = max(self.min_depth, min(self.max_depth, ideal))

    def _should_drop(self, low: float) -> bool:
        frame = self._frame_s
        if low < frame + self.margin:
            return False
        # Up to ~12% faster while far above the target, ~5% when close to it.
        return self._since_drop >= (8 if low >= 3 * frame else 20)

    def _take_next(self, now: float) -> np.ndarray:
        seq = self._next
        self._next = seq + 1
        self.stats.played += 1
        packet = self._packets.pop(seq, None)
        redundant = self._recovered.pop(seq, None)
        arrived = self._avail.pop(seq, None)
        if arrived is not None:
            slack = now - arrived
            self._record(slack)
            self._avg_wait += (slack - self._avg_wait) / 32
        if packet is not None:
            return self._decode(packet)
        if redundant is not None:
            self.stats.recovered += 1
            return self._decode(redundant)
        self._missed[seq] = (now, self._offset)
        if len(self._missed) > 64:
            self._missed = {s: v for s, v in self._missed.items() if s > seq - 64}
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
