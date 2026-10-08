import numpy as np
import pytest

from airwire.opus import OpusEncoder

FRAME = 480


def sine_frames(n: int, channels: int = 2, freq: float = 440.0, start: int = 0) -> list[np.ndarray]:
    frames = []
    for i in range(start, start + n):
        t = np.arange(i * FRAME, (i + 1) * FRAME) / 48_000
        mono = (8000 * np.sin(2 * np.pi * freq * t)).astype(np.int16)
        frames.append(np.repeat(mono[:, None], channels, axis=1))
    return frames


@pytest.fixture(scope="session")
def packets() -> list[bytes]:
    """60 consecutive 10 ms Opus packets of a 440 Hz stereo sine."""
    enc = OpusEncoder(channels=2)
    return [enc.encode(f) for f in sine_frames(60)]


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
