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

Fields: a listen step's "field" keeps its answer under that name: name, age, and
hope / fear for the two answers that can be traded. A choose step with a "field"
and "value_left" / "value_right" keeps the value for the side turned to; the
trade step uses "field": "valence" with values light / dark, and the ticket then
takes the hope for light or the fear for dark. An older "trade" field still works.

Where the ticket goes is a per-machine choice, so it lives in local.json:
  "TICKET_MODE": "print" | "screen" | "both"     (default print)
  "TICKET_SCREEN_HOLD": seconds on screen         (default 20)
With print, a ticket that fails to print is shown on screen instead.
"""
import argparse
import json
import os
import socket
import sys
import time

BED_FADE = 0.8

# per-machine, from local.json (see the docstring)
TICKET_MODE = "print"
TICKET_SCREEN_HOLD = 20
SCREEN_TICKET = "screen_ticket.png"


def load_local_settings(path="local.json"):
    """The show's own keys from local.json. The renderer reads the same file for
    its settings; each takes only what it knows and leaves the rest alone."""
    global TICKET_MODE, TICKET_SCREEN_HOLD
    if not os.path.isfile(path):
        return
    try:
        with open(path) as f:
            s = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[show] ignoring {path}: {e}")
        return
    mode = str(s.get("TICKET_MODE", TICKET_MODE)).lower()
    if mode not in ("print", "screen", "both"):
        print(f"[show] TICKET_MODE {mode!r} is not print, screen or both; using print")
        mode = "print"
    TICKET_MODE = mode
    TICKET_SCREEN_HOLD = float(s.get("TICKET_SCREEN_HOLD", TICKET_SCREEN_HOLD))
    print(f"[show] ticket mode: {TICKET_MODE}")
TICKET_DEFAULTS = {"enabled": True, "printer": "file:ticket.out", "width": 42,
                   "vault": "vault.jsonl", "valence": "dark", "style": "text",
                   "board": ["", "IT IS SPOKEN.", "TAKE YOUR BILL."], "hold": 8,
                   "delay": 0}          # seconds into the step before it prints


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
        self.ticket_at = None                # when a delayed ticket is due
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
    def trade_answer(self):
        """What this visitor is giving up, and which half of their future it is."""
        f = self.fields
        valence = f.get("valence") or self.ticket_cfg.get("valence", "dark")
        answer = f.get("hope" if valence == "light" else "fear") or f.get("trade")
        return answer, valence

    def print_ticket(self):
        """Compose the Bill of Exchange, then print it, show it, or both. A printer
        that is missing, jammed or unplugged must never stop the show, so
        everything here is guarded, and a failed print falls back to the screen."""
        cfg = self.ticket_cfg
        if not cfg.get("enabled", True):
            return
        answer, valence = self.trade_answer()
        if not answer:
            print("[show] no answer to put on a ticket")
            return
        try:
            import ticket
            # the exchange happens once, whatever the ticket is printed on
            parts = ticket.issue_parts(answer, valence, self.fields.get("name"),
                                       self.fields.get("age"),
                                       path=cfg.get("vault", "vault.jsonl"))
        except Exception as e:
            print(f"[show] ticket failed: {type(e).__name__}: {e}")
            return
        print(f"[show] ticket ({valence}): {parts['fortune']}")
        on_screen = TICKET_MODE in ("screen", "both")
        if TICKET_MODE in ("print", "both"):
            if not self.send_to_printer(parts, cfg) and not on_screen:
                print("[show] showing the ticket on screen instead")
                on_screen = True
        if on_screen:
            self.show_on_screen(parts, cfg)
        if cfg.get("board"):
            self.board(cfg["board"], cfg.get("hold"))

    def send_to_printer(self, parts, cfg):
        """True if the bytes went out. The printer can still fail after that
        without saying so (no paper, a jam); that is what screen mode is for."""
        target = cfg.get("printer", "file:ticket.out")
        try:
            import ticket
            if cfg.get("style") == "image":
                import ticket_art
                img = ticket_art.draw_ticket(parts["fortune"], parts["bearer"], parts["prior"],
                                             ratio=cfg.get("ratio", ticket_art.RATIO))
                img.save("last_ticket.png")              # always, for looking at later
                print("[show] " + ticket.send_raw(ticket_art.raster_bytes(img), target))
            else:
                lines = list(ticket.HEADER) + ["", parts["fortune"], ""]
                lines += [p for p in (parts["bearer"], parts["prior"]) if p]
                text = ticket.render(lines + ["", ticket.FINE_PRINT], int(cfg.get("width", 42)))
                with open("last_ticket.txt", "w") as f:
                    f.write(text + "\n")
                print("[show] " + ticket.send(text, target))
            return True
        except Exception as e:                            # printer trouble is not show trouble
            print(f"[show] print failed: {type(e).__name__}: {e}")
            return False

    def show_on_screen(self, parts, cfg):
        """Draw the ticket as the printer would, without the paper-walk margin or
        the upside-down flip, and have the renderer lay it over the picture."""
        try:
            import ticket_art
            img = ticket_art.draw_ticket(parts["fortune"], parts["bearer"], parts["prior"],
                                         ratio=cfg.get("ratio", ticket_art.RATIO),
                                         margin=0, flip=False)
            path = os.path.abspath(SCREEN_TICKET)
            img.convert("L").save(path)
        except Exception as e:
            print(f"[show] screen ticket failed: {type(e).__name__}: {e}")
            return
        self.send({"cmd": "ticket", "path": path, "hold": TICKET_SCREEN_HOLD})

    def attract(self, first=False):
        """Back to the powered-down state. From mid-show, via the sleep clip."""
        was_running = self.i is not None
        self.i, self.pending_answer = None, None
        self.fields = {}
        self.ticket_at = None                # a pending ticket does not survive a reset
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
        if self.ticket_at is not None and time.monotonic() >= self.ticket_at:
            self.ticket_at = None
            self.print_ticket()
            if self.step and self.step["type"] == "ticket":
                self.enter(self.i + 1)
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
        self.ticket_at = None
        if s.get("ticket") or s["type"] == "ticket":
            # a delay lets the clip get somewhere before the mechanism starts up,
            # so the printing lands under the right line rather than the first
            wait = float(s.get("ticket_delay", self.ticket_cfg.get("delay", 0)) or 0)
            if wait > 0:
                self.ticket_at = time.monotonic() + wait
                print(f"       ticket in {wait:.0f}s")
            else:
                self.print_ticket()
            if s["type"] == "ticket" and wait <= 0:
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
                self.send({"cmd": "ticket", "off": True})   # the last visitor's, if still up
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
                value = s.get("value_" + ev["side"])
                if s.get("field") and value is not None:  # e.g. valence: light / dark
                    self.fields[s["field"]] = value
                print(f"[show] chose for {s['id']}: {ev.get('label')!r}"
                      + (f" ({s['field']} = {value})" if s.get("field") and value is not None
                         else ""))
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
    ap.add_argument("--settings", default="local.json",
                    help="per-machine settings file, shared with the renderer")
    a = ap.parse_args()

    load_local_settings(a.settings)
    if not os.path.isfile(a.script):
        sys.exit(f"no script at {a.script}")
    script = json.load(open(a.script))
    sock = connect_waiting()

    show = Show(sock, script, a.trace)
    for ev in show.events():
        show.handle(ev)


if __name__ == "__main__":
    main()
