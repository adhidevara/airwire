import threading
import time

import numpy as np
import pytest

from airwire.capture import CaptureError, LoopbackCapture


class FakeRecorder:
    def __init__(self, mic):
        self.mic = mic

    def __enter__(self):
        if self.mic.fail_open:
            raise RuntimeError("Error 0x88890004")
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        time.sleep(0.001)
        self.mic.records += 1
        if self.mic.fail_after is not None and self.mic.records > self.mic.fail_after:
            raise RuntimeError("Error 0x88890004")  # AUDCLNT_E_DEVICE_INVALIDATED
        return np.full((numframes, 2), 0.25, np.float32)


class FakeMic:
    def __init__(self, fail_after=None, fail_open=False):
        self.fail_after, self.fail_open, self.records = fail_after, fail_open, 0

    def recorder(self, **_kw):
        return FakeRecorder(self)


def run_until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def make(mics, default_id=lambda: None, **attrs):
    frames, errors = [], []
    queue = list(mics)
    cap = LoopbackCapture(lambda pcm, _ms: frames.append(pcm), on_error=errors.append,
                          opener=lambda: queue.pop(0) if len(queue) > 1 else queue[0],
                          default_id=default_id)
    cap.RETRY_S = 0.01
    for k, v in attrs.items():
        setattr(cap, k, v)
    return cap, frames, errors


def test_reopens_after_the_device_disappears():
    cap, frames, errors = make([(FakeMic(fail_after=20), "A"), (FakeMic(), "A")])
    cap.start()
    assert run_until(lambda: len(frames) > 60)
    cap.stop()
    assert cap.restarts == 1 and errors == []
    assert frames[0].dtype == np.int16 and frames[0][0, 0] == int(0.25 * 32767)


def test_gives_up_after_repeated_failures():
    cap, _, errors = make([(FakeMic(fail_after=0), "A")])
    cap.start()
    assert run_until(lambda: errors)
    assert "0x88890004" in errors[0]
    assert cap.restarts == LoopbackCapture.MAX_FAILURES - 1


def test_follows_a_new_default_device():
    current = {"id": "A"}
    cap, frames, errors = make([(FakeMic(), "A"), (FakeMic(), "B")],
                               default_id=lambda: current["id"], CHECK_DEFAULT_EVERY=5)
    cap.start()
    assert run_until(lambda: len(frames) > 10)
    assert cap.restarts == 0
    current["id"] = "B"
    assert run_until(lambda: cap.restarts == 1)
    n = len(frames)
    assert run_until(lambda: len(frames) > n + 20)
    cap.stop()
    assert cap.restarts == 1 and errors == []  # stays on B, no reopen loop


def test_open_failure_raises():
    cap, _, _ = make([(FakeMic(fail_open=True), "A")])
    with pytest.raises(CaptureError, match="0x88890004"):
        cap.start()


def test_stop_ends_the_thread():
    cap, frames, _ = make([(FakeMic(), "A")])
    cap.start()
    assert run_until(lambda: frames)
    cap.stop()
    assert not any(t.name == "pc-capture" and t.is_alive() for t in threading.enumerate())
