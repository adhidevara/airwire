import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

import pytest

from airwire.files import FileService, Pairing, PairingError, PairingLocked, safe_filename, unique_path

CODE = "123456"


@pytest.fixture
def service(tmp_path):
    svc = FileService(tmp_path / "inbox", Pairing(CODE), host="127.0.0.1", port=0, name="test-pc")
    svc.start()
    yield svc
    svc.stop()


def call(svc, method, path, body=None, token=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{svc.port}{path}", data=body, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None and method == "POST":
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def pair(svc, code=CODE, device="Pixel"):
    status, _, body = call(svc, "POST", "/api/v1/pair",
                           json.dumps({"code": code, "device": device}).encode())
    return status, json.loads(body)


# ---- pure helpers -------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("photo.jpg", "photo.jpg"),
    ("../../etc/passwd", "passwd"),
    ("..\\..\\Windows\\win.ini", "win.ini"),
    ("..", "file"),
    ("", "file"),
    ('a<b>c:"d|e?f*.txt', "a_b_c__d_e_f_.txt"),
    ("trailing. ", "trailing"),
    ("CON.txt", "_CON.txt"),
    ("nul", "_nul"),
])
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_keeps_extension_when_truncating():
    name = safe_filename("x" * 300 + ".mp4")
    assert len(name) == 180 and name.endswith(".mp4")


def test_unique_path(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "a (1).txt").write_text("2")
    assert unique_path(tmp_path, "a.txt").name == "a (2).txt"
    assert unique_path(tmp_path, "b.txt").name == "b.txt"


def test_pairing_rate_limit_and_reset():
    t = [0.0]
    changes = []
    p = Pairing("000111", on_change=changes.append, clock=lambda: t[0])
    for _ in range(Pairing.MAX_FAILURES):
        with pytest.raises(PairingError):
            p.pair("999999", "x")
    with pytest.raises(PairingLocked):
        p.pair("000111", "x")              # locked even with the right code
    t[0] += Pairing.WINDOW_S + 1
    token = p.pair("000 111", "Phone")      # spaces are ignored
    assert p.device_for(token) == "Phone"
    assert token not in json.dumps(p.devices)   # only hashes are stored
    p.reset()
    assert p.device_for(token) is None and p.code != "" and len(changes) == 2


def test_pairing_rejects_malformed_saved_code():
    assert len(Pairing("abc").code) == 6


# ---- HTTP API -----------------------------------------------------------------

def test_requests_without_token_are_rejected(service):
    assert call(service, "GET", "/api/v1/hello")[0] == 401
    assert call(service, "PUT", "/api/v1/files/a.txt", b"x")[0] == 401
    assert call(service, "GET", "/api/v1/outbox", token="nope")[0] == 401
    assert call(service, "GET", "/nothing")[0] == 404


def test_pair_and_hello(service):
    status, body = pair(service, code="000000")
    assert status == 403
    status, body = pair(service)
    assert status == 200 and body["name"] == "test-pc"
    status, _, hello = call(service, "GET", "/api/v1/hello", token=body["token"])
    assert status == 200 and json.loads(hello)["device"] == "Pixel"


def test_upload_saves_file_with_safe_unique_name(service):
    token = pair(service)[1]["token"]
    data = os.urandom(700_000)
    for _ in range(2):
        status, _, body = call(service, "PUT", "/api/v1/files/" + quote("../my photo.jpg"),
                               data, token=token)
        assert status == 201
    names = sorted(p.name for p in service.files_dir.iterdir())
    assert names == ["my photo (1).jpg", "my photo.jpg"]
    reply = json.loads(body)
    assert reply["sha256"] == hashlib.sha256(data).hexdigest()
    assert (service.files_dir / "my photo.jpg").read_bytes() == data
    done = [t for t in service.transfers() if t.direction == "in"]
    assert len(done) == 2 and all(t.status == "done" and t.device == "Pixel" for t in done)


def test_truncated_upload_leaves_no_partial_file(service):
    import socket
    token = pair(service)[1]["token"]
    s = socket.create_connection(("127.0.0.1", service.port))
    s.sendall((f"PUT /api/v1/files/big.bin HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {token}\r\n"
               "Content-Length: 100000\r\n\r\n").encode() + b"x" * 1000)
    s.close()
    deadline = time.time() + 5
    while time.time() < deadline and not any(t.status == "failed" for t in service.transfers()):
        time.sleep(0.05)
    assert [t.status for t in service.transfers()] == ["failed"]
    assert list(service.files_dir.iterdir()) == []


def test_outbox_long_poll_download_and_ack(service, tmp_path):
    token = pair(service)[1]["token"]
    status, _, body = call(service, "GET", "/api/v1/outbox?since=-1&wait=0", token=token)
    first = json.loads(body)
    assert first["files"] == []

    src = tmp_path / "report.pdf"
    src.write_bytes(os.urandom(300_000))
    result = {}

    def poll():
        result["resp"] = call(service, "GET",
                              f"/api/v1/outbox?since={first['version']}&wait=10", token=token)

    poller = threading.Thread(target=poll)
    poller.start()
    time.sleep(0.2)
    service.send_to_phone([src, tmp_path / "missing.txt"])
    poller.join(5)
    listing = json.loads(result["resp"][2])
    assert [f["name"] for f in listing["files"]] == ["report.pdf"]
    item = listing["files"][0]

    status, headers, data = call(service, "GET", f"/api/v1/outbox/{item['id']}", token=token)
    assert status == 200 and data == src.read_bytes()
    assert headers["X-Airwire-Sha256"] == hashlib.sha256(data).hexdigest() == item["sha256"]

    assert call(service, "DELETE", f"/api/v1/outbox/{item['id']}", token=token)[0] == 204
    assert call(service, "DELETE", f"/api/v1/outbox/{item['id']}", token=token)[0] == 404
    out = [t for t in service.transfers() if t.direction == "out"]
    assert out[0].status == "done" and out[0].device == "Pixel"
    status, _, body = call(service, "GET", "/api/v1/outbox?since=-1&wait=0", token=token)
    assert json.loads(body)["files"] == []


def test_long_poll_times_out_without_changes(service):
    token = pair(service)[1]["token"]
    version = json.loads(call(service, "GET", "/api/v1/outbox?since=-1", token=token)[2])["version"]
    t0 = time.monotonic()
    status, _, body = call(service, "GET", f"/api/v1/outbox?since={version}&wait=0.5", token=token)
    assert status == 200 and 0.4 < time.monotonic() - t0 < 3
    assert json.loads(body) == {"version": version, "files": []}
