#!/usr/bin/env python3
"""
show.py - the show logic, driven by show_script.json.

Knows about Del Roar: which clip plays when, when the light comes up, when the
microphone is open, what the board says, and what the knob means. Holds no
drawing or audio code; it sends commands to renderer.py and reacts to its events.

  python3 show.py
  python3 show.py --trace
  python3 show.py --script other.json

ATTRACT   the attract clip loops (Del Roar hunched and frozen), the light is low
          and blue, the board cycles its attract lines
   | handset lifted
  steps   the script's steps in order; each can set the light and the board
   | the last step
ATTRACT   (usually after a sleep clip that brings him back down)

Step types:
  play    play a clip; move on when it ends
  ticket  compose and print the Bill of Exchange, then carry on
          (or put "ticket": true on any step to print as it begins)
  listen  open the mic, show a prompt, wait for speech, then arm the knob
  choose  arm the knob with no speech
  end     back to attract

Any step may carry:
  "light": "on" | "attract" | "dim"   or {"preset": "on", "flicker": true, "over": 1.0}
  "board": ["LINE 1", "LINE 2", "LINE 3"], with optional "hold": seconds
Listen steps also take "prompt" (two lines shown while waiting), "hint" (row 3
until speech arrives), and "bed" / "bed_fade" for the vamp. Knob steps take
"knob": {"left", "right"} and "on_left" / "on_right": next | again | goto:<id> | end.

Hanging up at any point plays the sleep clip, if the script names one, then
returns to attract.
"""
import argparse
import json
import os
import socket
import sys
import time

BED_FADE = 0.8
TICKET_DEFAULTS = {"enabled": True, "printer": "file:ticket.out", "width": 42,
                   "vault": "vault.jsonl", "valence": "dark",
                   "board": ["", "IT IS SPOKEN.", "TAKE YOUR BILL."], "hold": 8}


def wrap(text, cols=20, rows=2):
    """Fit an answer into the transcript rows, keeping the END of a long one."""
    words = text.upper().split()
    lines, cur = [], ""
    for word in words:
        if len(cur) + len(word) + (1 if cur else 0) <= cols:
            cur = f"{cur} {word}".strip()
        else:
            lines.append(cur)
            cur = word[:cols]
    if cur:
        lines.append(cur)
    lines = lines[-rows:]
    return lines + [""] * (rows - len(lines))


class Show:
    def __init__(self, sock, script, trace=False):
        self.sock, self.trace = sock, trace
        self.idle = script.get("idle", "idle")
        self.attract_cfg = script.get("attract", {})
        self.sleep_clip = script.get("sleep_clip")
        self.steps = script["steps"]
        self.by_id = {s["id"]: i for i, s in enumerate(self.steps)}
        self.i = None
        self.answers = {}
        self.fields = {}                     # name / age / trade, from step "field" tags
        self.missing_clips = set()           # reported by the renderer; fall back rather than wait
        self.ticket_cfg = {**TICKET_DEFAULTS, **script.get("ticket", {})}
        self.pending_answer = None
        self.buf = b""
        self.cycle_at = 0.0
        self.cycle_i = 0
        self.going_to_sleep = False
        self.attract(first=True)

    # ---------------- plumbing ----------------
    def send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def events(self):
        """Renderer events, and a None every quarter second so timers can run."""
        self.sock.settimeout(0.25)
        while True:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                yield None
                continue
            if not chunk:
                return
            self.buf += chunk
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if line.strip():
                    yield json.loads(line)

    @property
    def step(self):
        return self.steps[self.i] if self.i is not None else None

    def light(self, spec):
        if not spec:
            return
        cmd = {"cmd": "light"}
        cmd.update({"preset": spec} if isinstance(spec, str) else spec)
        self.send(cmd)

    def board(self, lines, hold=None):
        cmd = {"cmd": "board", "lines": lines}
        if hold:
            cmd["hold"] = hold
        self.send(cmd)

    def mic(self, on):
        self.send({"cmd": "stt", "on": on})

    # ---------------- attract ----------------
    def print_ticket(self):
        """Compose and print the Bill of Exchange. A printer that is missing, jammed
        or unplugged must never stop the show, so everything here is guarded."""
        cfg = self.ticket_cfg
        if not cfg.get("enabled", True):
            return
        answer = self.fields.get("trade") or (list(self.answers.values()) or [None])[-1]
        if not answer:
            print("[show] no answer to put on a ticket")
            return
        try:
            import ticket
            lines = ticket.issue(answer, cfg.get("valence", "dark"),
                                 self.fields.get("name"), self.fields.get("age"),
                                 path=cfg.get("vault", "vault.jsonl"))
            text = ticket.render(lines, int(cfg.get("width", 42)))
            with open("last_ticket.txt", "w") as f:       # always, for looking at later
                f.write(text + "\n")
            print("[show] ticket:\n" + text)
            print("[show] " + ticket.send(text, cfg.get("printer", "file:ticket.out")))
        except Exception as e:                            # printer trouble is not show trouble
            print(f"[show] ticket failed: {type(e).__name__}: {e}")
        if cfg.get("board"):
            self.board(cfg["board"], cfg.get("hold"))

    def attract(self, first=False):
        """Back to the powered-down state. From mid-show, via the sleep clip."""
        was_running = self.i is not None
        self.i, self.pending_answer = None, None
        self.fields = {}
        self.mic(False)
        self.send({"cmd": "knob", "off": True})
        self.send({"cmd": "audio", "stop": True, "fade_out": BED_FADE})
        clip = self.attract_cfg.get("clip", self.idle)
        if self.missing_clips and clip in self.missing_clips:
            clip = self.idle[0] if isinstance(self.idle, list) else self.idle
        if was_running and self.sleep_clip and self.sleep_clip not in self.missing_clips \
                and not self.going_to_sleep:
            self.going_to_sleep = True
            self.send({"cmd": "play", "clip": self.sleep_clip, "then": clip, "now": True})
            self.light({"preset": self.attract_cfg.get("light", "attract"), "over": 2.5})
        else:
            self.send({"cmd": "loop", "clip": clip, "now": True})
            self.light({"preset": self.attract_cfg.get("light", "attract"),
                        "over": 0.01 if first else 1.5})
        self.cycle_i, self.cycle_at = 0, 0.0
        self._attract_board()
        print("[show] ATTRACT")

    def _attract_board(self):
        cycle = self.attract_cfg.get("cycle") or [self.attract_cfg.get("board", ["", "LIFT THE HANDSET", ""])]
        self.board(cycle[self.cycle_i % len(cycle)])
        self.cycle_i += 1
        self.cycle_at = time.monotonic() + float(self.attract_cfg.get("every", 6.0))

    def tick(self):
        if self.i is None and time.monotonic() >= self.cycle_at and \
                len(self.attract_cfg.get("cycle") or []) > 1:
            self._attract_board()

    # ---------------- steps ----------------
    def enter(self, index):
        if index is None or index >= len(self.steps):
            return self.attract()
        self.i = index
        s = self.step
        print(f"[show] step {s['id']} ({s['type']})")
        self.light(s.get("light"))
        if s["type"] != "listen":
            self.mic(False)
        if s.get("ticket") or s["type"] == "ticket":
            self.print_ticket()
            if s["type"] == "ticket":
                return self.enter(self.i + 1)

        if s["type"] == "play":
            if s.get("board"):
                self.board(s["board"], s.get("hold"))
            else:
                self.send({"cmd": "clear"})
            self.send({"cmd": "play", "clip": s["clip"], "then": self.idle, "now": True})
        elif s["type"] == "listen":
            self.pending_answer = None
            prompt = s.get("prompt", ["", ""])
            self.send({"cmd": "board", "rows": {
                "0": {"text": prompt[0] if len(prompt) > 0 else "", "align": "center"},
                "1": {"text": prompt[1] if len(prompt) > 1 else "", "align": "center"},
                "2": {"text": s.get("hint", ""), "align": "center"}}})
            self.send({"cmd": "loop", "clip": self.idle, "cues": False})
            if s.get("bed"):
                self.send({"cmd": "audio", "track": s["bed"], "fade_in": 0.6, "loop": True})
            self.mic(True)
            print("       listening ...")
        elif s["type"] == "choose":
            if s.get("board"):
                self.board(s["board"])
            if s.get("bed"):
                self.send({"cmd": "audio", "track": s["bed"], "fade_in": 0.6, "loop": True})
            self.arm_knob()
        elif s["type"] == "end":
            self.attract()

    def arm_knob(self):
        k = self.step.get("knob")
        if not k:
            return self.follow("next")
        self.send({"cmd": "knob", "left": k["left"], "right": k["right"]})

    def follow(self, target):
        self.send({"cmd": "knob", "off": True})
        s = self.step
        if target != "again":
            self.mic(False)
            if s and s.get("bed"):
                self.send({"cmd": "audio", "stop": True,
                           "fade_out": s.get("bed_fade", BED_FADE)})
        if target == "next":
            self.enter(self.i + 1)
        elif target == "again":
            self.enter(self.i)
        elif target == "end":
            self.attract()
        elif target.startswith("goto:"):
            dest = target.split(":", 1)[1]
            if dest not in self.by_id:
                print(f"[show] unknown step id in script: {dest}")
                return self.attract()
            self.enter(self.by_id[dest])
        else:
            print(f"[show] unknown target: {target}")
            self.attract()

    # ---------------- events ----------------
    def handle(self, ev):
        if ev is None:
            return self.tick()
        if self.trace:
            print("  < " + json.dumps(ev))
        kind = ev.get("event")

        if kind == "error":
            message = ev.get("message", "")
            if "clip not found" in message:
                name = os.path.splitext(os.path.basename(message.split(":")[-1].strip()))[0]
                if name not in self.missing_clips:
                    self.missing_clips.add(name)
                    print(f"[show] missing clip: {name}")

        if kind == "input" and ev["name"] == "handset":
            if ev["state"] == "up" and self.i is None:
                self.going_to_sleep = False
                self.enter(0)
            elif ev["state"] == "down" and self.i is not None:
                print("[show] handset down; resetting")
                self.attract()
            return

        if self.i is None:
            if kind == "ended" and ev.get("clip") == self.sleep_clip:
                self.going_to_sleep = False
            return
        s = self.step

        if kind == "ended" and s["type"] == "play" and ev.get("clip") == s["clip"]:
            self.enter(self.i + 1)

        elif kind == "error" and s["type"] == "play" and s["clip"] in ev.get("message", ""):
            # a clip that will never play can't be waited on: say so and carry on
            print(f"[show] {s['clip']} is missing; skipping that step")
            self.enter(self.i + 1)

        elif kind == "utterance" and s["type"] == "listen":
            text = ev.get("text", "").strip()
            if text:
                self.pending_answer = text
                l0, l1 = wrap(text)
                self.send({"cmd": "board", "rows": {"0": {"text": l0, "align": "center"},
                                                    "1": {"text": l1, "align": "center"}}})
                self.arm_knob()

        elif kind == "activated":
            if s["type"] == "listen" and self.pending_answer is None:
                return
            if s["type"] == "listen" and ev["side"] == "right":
                self.answers[s["id"]] = self.pending_answer
                if s.get("field"):                    # name / age / trade
                    self.fields[s["field"]] = self.pending_answer
                print(f"[show] kept for {s['id']}"
                      + (f" ({s['field']})" if s.get("field") else "")
                      + f": {self.pending_answer!r}")
            if s["type"] == "choose":
                self.answers[s["id"]] = ev.get("label")
                print(f"[show] chose for {s['id']}: {ev.get('label')!r}")
            self.follow(s.get("on_" + ev["side"], "next"))


def connect(path=None, port=None):
    """The renderer's socket. DELROAR_SOCKET / DELROAR_PORT override the defaults,
    which is how a test or a second copy talks to its own renderer."""
    path = path or os.environ.get("DELROAR_SOCKET", "/tmp/delroar-renderer.sock")
    port = int(port or os.environ.get("DELROAR_PORT", 8765))
    if hasattr(socket, "AF_UNIX"):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
            return s
        except OSError:
            pass
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
    return s



def connect_waiting(timeout=None, quiet=False):
    """Wait for the renderer rather than giving up. At boot this process often
    starts first, and exiting immediately made systemd's restart limit kick in and
    stop trying altogether."""
    started, said = time.monotonic(), False
    while True:
        try:
            return connect()
        except OSError:
            if timeout is not None and time.monotonic() - started > timeout:
                raise
            if not said and not quiet:
                print("waiting for the renderer ...")
                said = True
            time.sleep(2)

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", default="show_script.json")
    ap.add_argument("--trace", action="store_true")
    a = ap.parse_args()

    if not os.path.isfile(a.script):
        sys.exit(f"no script at {a.script}")
    script = json.load(open(a.script))
    sock = connect_waiting()

    show = Show(sock, script, a.trace)
    for ev in show.events():
        show.handle(ev)


if __name__ == "__main__":
    main()
