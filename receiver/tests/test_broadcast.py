import hashlib

import numpy as np
import pytest

from airwire import protocol as p
from airwire.broadcast import PcAudioBroadcaster
from airwire.capture import CaptureError
from airwire.files import Pairing

PHONE = ("192.168.1.20", 40000)


class FakeCapture:
    instances: list["FakeCapture"] = []
    fail = False

    def __init__(self, on_frame, *, on_error=None):
        self.on_frame, self.on_error = on_frame, on_error
        self.running = False
        FakeCapture.instances.append(self)

    def start(self):
        if FakeCapture.fail:
            raise CaptureError("no loopback device")
        self.running = True

    def stop(self):
        self.running = False

    def frame(self, value=1000):
        self.on_frame(np.full((480, 2), value, np.int16), 5)


@pytest.fixture
def setup(clock):
    FakeCapture.instances.clear()
    FakeCapture.fail = False
    pairing = Pairing("123456")
    token = pairing.pair("123456", "Pixel 8")
    sent: list[tuple[p.Packet, tuple]] = []
    b = PcAudioBroadcaster(pairing, lambda pkt, addr: sent.append((pkt, addr)), name="My-PC",
                           capture_factory=FakeCapture, clock=clock)
    key = hashlib.sha256(token.encode()).digest()
    return b, key, sent


def listen(key, counter, session=7, kbps=96):
    return p.Listen(session, kbps, counter, p.listen_mac(key, session, kbps, counter), "Pixel 8")


def test_paired_phone_gets_hello_and_audio(setup):
    b, key, sent = setup
    b.handle_listen(listen(key, 1), PHONE)
    [info] = b.listeners()
    assert info.device == "Pixel 8" and info.bitrate_kbps == 96
    cap = FakeCapture.instances[-1]
    assert cap.running
    cap.frame()
    cap.frame()
    kinds = [type(pkt).__name__ for pkt, addr in sent if addr == PHONE]
    assert kinds == ["Hello", "Audio", "Audio"]
    hello = sent[0][0]
    assert hello.source == p.SOURCE_PC and hello.name == "My-PC"
    a0, a1 = sent[1][0], sent[2][0]
    assert (a0.seq, a1.seq) == (0, 1) and a1.redundant == a0.primary
    assert a0.session == a1.session == hello.session


def test_wrong_key_is_refused(setup):
    b, _, sent = setup
    b.handle_listen(listen(b"\x00" * 32, 1), PHONE)
    assert b.listeners() == []
    assert sent == [(p.Refuse(7, p.REFUSE_NOT_PAIRED), PHONE)]


def test_replayed_listen_is_refused(setup):
    b, key, sent = setup
    b.handle_listen(listen(key, 5), PHONE)
    sent.clear()
    b.handle_listen(listen(key, 5), ("10.0.0.66", 40000))  # same packet from someone else
    assert sent == [(p.Refuse(7, p.REFUSE_NOT_PAIRED), ("10.0.0.66", 40000))]
    assert [l.addr for l in b.listeners()] == [PHONE]


def test_disabled_refuses(setup):
    b, key, sent = setup
    b.enabled = False
    b.handle_listen(listen(key, 1), PHONE)
    assert sent == [(p.Refuse(7, p.REFUSE_DISABLED), PHONE)]


def test_stop_all_refuses_that_session_and_stops_capture(setup):
    b, key, sent = setup
    b.handle_listen(listen(key, 1), PHONE)
    b.stop_all()
    assert b.listeners() == [] and not FakeCapture.instances[-1].running
    b.handle_listen(listen(key, 2), PHONE)
    assert sent[-1] == (p.Refuse(7, p.REFUSE_STOPPED), PHONE)
    b.handle_listen(listen(key, 3, session=8), PHONE)  # the phone starts again
    assert len(b.listeners()) == 1


def test_bye_and_timeout_remove_listener(setup, clock):
    b, key, _ = setup
    b.handle_listen(listen(key, 1), PHONE)
    assert b.handle_bye(p.Bye(7), PHONE)
    assert b.listeners() == [] and not FakeCapture.instances[-1].running
    b.handle_listen(listen(key, 2), PHONE)
    clock.t += PcAudioBroadcaster.TIMEOUT_S + 0.1
    b.tick()
    assert b.listeners() == []


def test_ping_updates_stats_and_gets_pong(setup):
    b, key, sent = setup
    assert not b.handle_ping(p.Ping(1, 0, 0, 0), PHONE)
    b.handle_listen(listen(key, 1), PHONE)
    assert b.handle_ping(p.Ping(99, 12, 3400, 30), PHONE)
    assert sent[-1] == (p.Pong(99), PHONE)
    [info] = b.listeners()
    assert (info.loss_permille, info.jitter_us, info.buffer_ms) == (12, 3400, 30)


def test_bitrate_change_applies_on_capture_thread(setup):
    b, key, sent = setup
    b.handle_listen(listen(key, 1, kbps=64), PHONE)
    b.handle_listen(listen(key, 2, kbps=160), PHONE)
    FakeCapture.instances[-1].frame()
    hello = next(pkt for pkt, _ in sent if isinstance(pkt, p.Hello))
    assert hello.bitrate_kbps == 160


def test_capture_failure_refuses(setup):
    b, key, sent = setup
    FakeCapture.fail = True
    b.handle_listen(listen(key, 1), PHONE)
    assert b.listeners() == []
    assert sent == [(p.Refuse(7, p.REFUSE_NO_CAPTURE), PHONE)]
    assert "loopback" in b.capture_error


def test_phone_back_after_timeout_keeps_its_stream(setup, clock):
    b, key, sent = setup
    b.handle_listen(listen(key, 1), PHONE)
    FakeCapture.instances[-1].frame()
    first = next(pkt for pkt, _ in sent if isinstance(pkt, p.Audio))
    clock.t += PcAudioBroadcaster.TIMEOUT_S + 0.1
    b.tick()
    assert b.listeners() == []
    sent.clear()
    b.handle_listen(listen(key, 2), PHONE)  # same LISTEN session, Wi-Fi recovered
    FakeCapture.instances[-1].frame()
    again = next(pkt for pkt, _ in sent if isinstance(pkt, p.Audio))
    assert again.session == first.session and again.seq == first.seq + 1
