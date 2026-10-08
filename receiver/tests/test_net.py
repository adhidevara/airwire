import socket
import time

import pytest

from airwire import protocol as p
from airwire.jitter_buffer import JitterBuffer
from airwire.net import ReceiverServer
from airwire.stats import NetStats


@pytest.fixture
def server():
    srv = ReceiverServer(JitterBuffer(), NetStats(), host="127.0.0.1", port=0, name="test-pc")
    srv.start()
    yield srv
    srv.stop()


def client() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2)
    return s


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def hello(session=1, name="Phone"):
    return p.encode(p.Hello(session, 48000, 2, 10, 128, p.SOURCE_INTERNAL, 20, name))


def test_discover_gets_offer(server):
    c = client()
    c.sendto(p.encode(p.Discover(77)), ("127.0.0.1", server.port))
    offer = p.decode(c.recv(1500))
    assert offer == p.Offer(77, server.port, "test-pc")


def test_locks_first_sender_and_answers_ping(server, packets):
    phone, intruder = client(), client()
    addr = ("127.0.0.1", server.port)
    phone.sendto(hello(), addr)
    for seq in range(5):
        phone.sendto(p.encode(p.Audio(1, seq, packets[seq], packets[seq - 1] if seq else None)),
                     addr)
    intruder.sendto(hello(session=9, name="Other"), addr)

    assert wait_for(lambda: server.jitter.depth == 5)
    s = server.sender
    assert s.name == "Phone" and s.addr == phone.getsockname()
    assert wait_for(lambda: server.foreign_packets == 1)

    ping = p.decode(phone.recv(1500))
    assert isinstance(ping, p.Ping)
    phone.sendto(p.encode(p.Pong(ping.t_ns)), addr)
    assert wait_for(lambda: server.stats.rtt_ms is not None)


def test_new_session_resets_and_bye_disconnects(server, packets):
    phone = client()
    addr = ("127.0.0.1", server.port)
    phone.sendto(p.encode(p.Audio(1, 0, packets[0])), addr)
    phone.sendto(p.encode(p.Audio(1, 1, packets[1])), addr)
    assert wait_for(lambda: server.jitter.depth == 2)
    phone.sendto(p.encode(p.Audio(2, 0, packets[0])), addr)
    assert wait_for(lambda: server.sender.session == 2 and server.jitter.depth == 1)
    phone.sendto(p.encode(p.Bye(2)), addr)
    assert wait_for(lambda: server.sender is None)


def test_garbage_is_counted_not_fatal(server):
    c = client()
    c.sendto(b"hello world", ("127.0.0.1", server.port))
    assert wait_for(lambda: server.invalid_packets == 1)
