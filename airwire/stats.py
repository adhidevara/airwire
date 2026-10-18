"""Network statistics for the locked sender: loss, jitter (RFC 3550), RTT, rate."""

from __future__ import annotations

import threading
import time
from typing import Callable


class NetStats:
    def __init__(self, frame_ms: float = 10.0, clock: Callable[[], float] = time.monotonic):
        self.frame_ms = frame_ms
        self._clock = clock
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.received = 0
            self.bytes = 0
            self._base_seq: int | None = None
            self._max_seq: int | None = None
            self._last_transit: float | None = None
            self._jitter_s = 0.0
            self.rtt_ms: float | None = None
            self.loss = 0.0
            self.kbps = 0.0
            self.packets_per_s = 0.0
            self._mark_time = self._clock()
            self._mark_expected = 0
            self._mark_received = 0
            self._mark_bytes = 0

    @property
    def jitter_ms(self) -> float:
        return self._jitter_s * 1000

    @property
    def expected(self) -> int:
        if self._base_seq is None:
            return 0
        return self._max_seq - self._base_seq + 1

    def on_audio(self, seq: int, nbytes: int, now: float | None = None) -> None:
        now = self._clock() if now is None else now
        with self._lock:
            self.received += 1
            self.bytes += nbytes
            if self._base_seq is None:
                self._base_seq = self._max_seq = seq
            elif seq > self._max_seq:
                self._max_seq = seq
            elif seq < self._base_seq:
                self._base_seq = seq
            # RFC 3550 interarrival jitter, with the frame counter as the sender clock.
            transit = now - seq * self.frame_ms / 1000
            if self._last_transit is not None:
                d = abs(transit - self._last_transit)
                self._jitter_s += (d - self._jitter_s) / 16
            self._last_transit = transit

    def on_pong(self, sent_ns: int, now_ns: int | None = None) -> None:
        now_ns = time.monotonic_ns() if now_ns is None else now_ns
        rtt = (now_ns - sent_ns) / 1e6
        if rtt < 0:
            return
        self.rtt_ms = rtt if self.rtt_ms is None else self.rtt_ms + (rtt - self.rtt_ms) / 4

    def sample(self, now: float | None = None) -> None:
        """Close the current measurement interval (called every ~0.5 s)."""
        now = self._clock() if now is None else now
        with self._lock:
            elapsed = now - self._mark_time
            if elapsed <= 0:
                return
            expected, received, nbytes = self.expected, self.received, self.bytes
            d_expected = expected - self._mark_expected
            d_received = received - self._mark_received
            if d_expected > 0:
                self.loss = max(0.0, min(1.0, (d_expected - d_received) / d_expected))
            self.packets_per_s = d_received / elapsed
            self.kbps = (nbytes - self._mark_bytes) * 8 / 1000 / elapsed
            self._mark_time = now
            self._mark_expected = expected
            self._mark_received = received
            self._mark_bytes = nbytes
