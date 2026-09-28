"""The control socket: commands in, events out, and clients that never read."""
import json
import socket
import time

from control import ControlServer


def connect(path):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(path)
    return s


def test_commands_arrive_and_bad_json_is_answered(tmp_path):
    path = str(tmp_path / "s.sock")
    srv = ControlServer(path)
    c = connect(path)
    c.sendall(b'{"cmd":"board","lines":["HELLO"]}\n{"cmd": oops\n')
    time.sleep(0.3)
    assert srv.poll() == [{"cmd": "board", "lines": ["HELLO"]}]
    c.settimeout(1)
    assert "error" in c.recv(4096).decode()


def test_a_client_that_never_reads_cannot_stall_the_renderer(tmp_path):
    path = str(tmp_path / "s.sock")
    srv = ControlServer(path)
    connect(path)                      # like stt.py: sends, never reads
    time.sleep(0.2)
    start = time.time()
    for i in range(20000):
        srv.emit({"event": "settled", "board": ["X" * 20] * 3, "n": i})
    assert time.time() - start < 5, "emit blocked: this is what froze the show"


def test_events_reach_a_client_that_reads(tmp_path):
    path = str(tmp_path / "s.sock")
    srv = ControlServer(path)
    c = connect(path)
    time.sleep(0.2)
    srv.emit({"event": "started", "clip": "segment_1"})
    c.settimeout(2)
    assert json.loads(c.recv(4096).decode().splitlines()[0])["clip"] == "segment_1"
