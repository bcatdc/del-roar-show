"""Show logic: the shape of a visit, driven through a stand-in renderer."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from control import ControlServer

ROOT = Path(__file__).resolve().parent.parent


def start_show(tmp_path, script):
    """A stand-in renderer, and show.py pointed at it."""
    sock_path = str(tmp_path / "s.sock")
    srv = ControlServer(sock_path)
    spath = tmp_path / "script.json"
    spath.write_text(json.dumps(script))
    env = {**os.environ, "DELROAR_SOCKET": sock_path}
    proc = subprocess.Popen([sys.executable, str(ROOT / "show.py"), "--script", str(spath)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            cwd=str(tmp_path), env=env)
    return srv, proc


SCRIPT = {
    "attract": {"clip": "attract", "light": "attract",
                "cycle": [["", "LIFT THE HANDSET", ""]]},
    "sleep_clip": "sleep",
    "idle": ["idle_a"],
    "steps": [
        {"id": "wake", "type": "play", "clip": "wake",
         "light": {"preset": "on", "flicker": True}},
        {"id": "segment_1", "type": "play", "clip": "segment_1"},
        {"id": "listen_1", "type": "listen", "bed": "vamp_1", "prompt": ["SAY A THING", ""],
         "hint": "SPEAK NOW", "knob": {"left": "SAY IT AGAIN", "right": "LET IT STAND"},
         "on_left": "again", "on_right": "next"},
        {"id": "done", "type": "end"},
    ],
}


def cmds(srv, wait=0.35):
    time.sleep(wait)
    return srv.poll()


def test_a_visit_runs_in_order(tmp_path):
    srv, proc = start_show(tmp_path, SCRIPT)
    try:
        start = cmds(srv, 0.8)
        assert any(c["cmd"] == "loop" and c["clip"] == "attract" for c in start)
        assert any(c["cmd"] == "light" for c in start)
        assert any(c["cmd"] == "stt" and c["on"] is False for c in start)

        srv.emit({"event": "input", "name": "handset", "state": "up"})
        assert any(c["cmd"] == "play" and c["clip"] == "wake" for c in cmds(srv))

        srv.emit({"event": "ended", "clip": "wake"})
        assert any(c["cmd"] == "play" and c["clip"] == "segment_1" for c in cmds(srv))

        srv.emit({"event": "ended", "clip": "segment_1"})
        listen = cmds(srv)
        assert any(c["cmd"] == "stt" and c["on"] is True for c in listen), "mic never opened"
        assert any(c["cmd"] == "audio" and c.get("track") == "vamp_1" for c in listen)

        srv.emit({"event": "utterance", "text": "the last train home"})
        assert any(c["cmd"] == "knob" and c.get("left") for c in cmds(srv))

        srv.emit({"event": "activated", "side": "right"})
        done = cmds(srv)
        assert any(c["cmd"] == "stt" and c["on"] is False for c in done), "mic left open"
        assert any(c["cmd"] == "play" and c["clip"] == "sleep" for c in done)
    finally:
        proc.terminate()


def test_speech_during_a_clip_never_reaches_the_board(tmp_path):
    srv, proc = start_show(tmp_path, SCRIPT)
    try:
        cmds(srv, 0.8)
        srv.emit({"event": "input", "name": "handset", "state": "up"})
        cmds(srv)
        srv.emit({"event": "utterance", "text": "the trump presidency"})
        assert not [c for c in cmds(srv) if c["cmd"] == "board"], "mis-heard speech hit the board"
    finally:
        proc.terminate()


TICKET_SCRIPT = {
    "attract": {"clip": "attract", "cycle": [["", "LIFT THE HANDSET", ""]]},
    "idle": ["idle_a"],
    "ticket": {"printer": "file:ticket.out", "width": 42, "valence": "dark"},
    "steps": [
        {"id": "ask_name", "type": "listen", "field": "name",
         "knob": {"left": "SAY IT AGAIN", "right": "LET IT STAND"}, "on_right": "next"},
        {"id": "ask_age", "type": "listen", "field": "age",
         "knob": {"left": "SAY IT AGAIN", "right": "LET IT STAND"}, "on_right": "next"},
        {"id": "ask_fear", "type": "listen", "field": "trade",
         "knob": {"left": "SAY IT AGAIN", "right": "LET IT STAND"}, "on_right": "next"},
        {"id": "closing", "type": "play", "clip": "segment_7", "ticket": True},
        {"id": "done", "type": "end"},
    ],
}


def answer(srv, text):
    srv.emit({"event": "utterance", "text": text})
    time.sleep(0.3)
    srv.emit({"event": "activated", "side": "right"})
    time.sleep(0.3)


def test_a_visit_prints_a_ticket(tmp_path):
    srv, proc = start_show(tmp_path, TICKET_SCRIPT)
    try:
        cmds(srv, 0.8)
        srv.emit({"event": "input", "name": "handset", "state": "up"})
        cmds(srv)
        answer(srv, "Ben.")
        answer(srv, "43.")
        answer(srv, "the last train home")
        cmds(srv, 0.6)
        printed = (tmp_path / "last_ticket.txt").read_text()
        assert "BILL OF EXCHANGE" in printed
        assert "BEARER: BEN, 43" in printed
        assert "the last train home" not in printed, "gave them back their own answer"
        assert (tmp_path / "ticket.out").exists(), "nothing sent to the printer"
        assert max(len(l) for l in printed.splitlines()) <= 42, "too wide for 80mm paper"
    finally:
        proc.terminate()


def test_a_minor_is_not_named_in_the_vault(tmp_path):
    srv, proc = start_show(tmp_path, TICKET_SCRIPT)
    try:
        cmds(srv, 0.8)
        srv.emit({"event": "input", "name": "handset", "state": "up"})
        cmds(srv)
        answer(srv, "Maya")
        answer(srv, "fifteen")
        answer(srv, "a door left unlocked")
        cmds(srv, 0.6)
        assert "MAYA, 15" in (tmp_path / "last_ticket.txt").read_text()
        rows = [json.loads(l) for l in (tmp_path / "vault.jsonl").read_text().splitlines() if l]
        assert rows and all(r["name"] is None for r in rows if r.get("age") == 15)
    finally:
        proc.terminate()


VALENCE_SCRIPT = {
    "idle": ["idle_a"],
    "steps": [
        {"id": "ask_hope", "type": "listen", "field": "hope",
         "knob": {"left": "SAY IT AGAIN", "right": "LET IT STAND"}, "on_right": "next"},
        {"id": "ask_fear", "type": "listen", "field": "fear",
         "knob": {"left": "SAY IT AGAIN", "right": "LET IT STAND"}, "on_right": "next"},
        {"id": "trade", "type": "choose", "field": "valence",
         "knob": {"left": "BRIGHT MOMENT", "right": "DARK MOMENT"},
         "value_left": "light", "value_right": "dark"},
        {"id": "closing", "type": "play", "clip": "segment_7", "ticket": True},
        {"id": "done", "type": "end"},
    ],
    "ticket": {"printer": "file:ticket.out"},
}


def run_trade(tmp_path, side, local=None, script=VALENCE_SCRIPT):
    if local is not None:
        (tmp_path / "local.json").write_text(json.dumps(local))
    srv, proc = start_show(tmp_path, script)
    try:
        cmds(srv, 0.8)
        srv.emit({"event": "input", "name": "handset", "state": "up"})
        cmds(srv)
        answer(srv, "a trip to the sea")
        answer(srv, "the dentist")
        srv.emit({"event": "activated", "side": side})
        return cmds(srv, 0.8)
    finally:
        proc.terminate()


def vault_rows(tmp_path):
    return [json.loads(l) for l in (tmp_path / "vault.jsonl").read_text().splitlines() if l]


def test_the_trade_gives_up_the_half_that_was_chosen(tmp_path):
    run_trade(tmp_path, "left")                   # BRIGHT MOMENT
    rows = vault_rows(tmp_path)
    assert [(r["text"], r["valence"]) for r in rows] == [("a trip to the sea", "light")]
    assert rows[0]["moderated"] is False


def test_screen_mode_shows_the_ticket_and_does_not_print(tmp_path):
    out = run_trade(tmp_path, "right", local={"TICKET_MODE": "screen"})
    shown = [c for c in out if c["cmd"] == "ticket" and not c.get("off")]
    assert shown and os.path.isfile(shown[0]["path"])
    assert not (tmp_path / "ticket.out").exists()
    assert vault_rows(tmp_path)[0]["valence"] == "dark"


def test_a_failed_print_falls_back_to_the_screen(tmp_path):
    script = {**VALENCE_SCRIPT, "ticket": {"printer": "nonsense:here"}}
    out = run_trade(tmp_path, "right", script=script)
    assert any(c["cmd"] == "ticket" and not c.get("off") for c in out)
