#!/usr/bin/env python3
"""
stt.py - Whisper speech-to-text, pushed to the renderer's board.

Listens on the microphone, waits for the speaker to stop, transcribes that
chunk, and sends the words to rows 0 and 1. It deliberately does NOT stream
interim results: a full line takes about 1.7s to flap, so live word-by-word
updates would clatter continuously and never catch up. Del Roar transmits what
was spoken; he doesn't take dictation.

  python3 stt.py --list-devices
  python3 stt.py --device 3
  python3 stt.py --device 3 --model small.en --rows 0 1

It also emits its own line to stdout, and sends an "utterance" board update plus
a {"cmd":"board"} to the renderer. Show logic can either run this itself or read
its stdout; see show.py for the wiring used in testing.

Install (all free):
  python3 -m pip install faster-whisper sounddevice numpy
faster-whisper is a fast reimplementation of OpenAI's Whisper
(github.com/SYSTRAN/faster-whisper). Models download themselves on first run:
tiny.en is about 75MB, small.en about 500MB. On a NUC with no GPU, use
tiny.en or base.en and compute_type="int8".
"""
import argparse
import json
import os
import queue
import socket
import sys
import time

import numpy as np

SR = 16000
BLOCK = 0.05                 # seconds per audio block
SILENCE_HOLD = 1.1           # seconds of quiet that ends an utterance; long enough
                             # that a short pause mid-answer doesn't split it
MIN_SPEECH = 0.4             # ignore anything shorter than this
LEVEL_GATE = 0.012           # RMS above this counts as speech
MAX_UTTERANCE = 12.0         # stop listening after this long


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


def send(sock, obj):
    if sock:
        try:
            sock.sendall((json.dumps(obj) + "\n").encode())
        except OSError:
            pass


def wrap(text, cols, rows):
    """Fit words into `rows` lines of `cols`, keeping the END of a long answer.
    The prompts end in 'the', so the meaningful part is what came last."""
    words = text.upper().split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + (1 if cur else 0) <= cols:
            cur = f"{cur} {w}".strip()
        else:
            lines.append(cur)
            cur = w[:cols]
    if cur:
        lines.append(cur)
    return lines[-rows:] if len(lines) > rows else lines + [""] * (rows - len(lines))



def connect_waiting(timeout=None):
    """Wait for the renderer rather than giving up. At boot this process often
    starts first, and exiting immediately made systemd's restart limit kick in."""
    started, said = time.monotonic(), False
    while True:
        try:
            return connect()
        except OSError:
            if timeout is not None and time.monotonic() - started > timeout:
                raise
            if not said:
                print("[stt] waiting for the renderer ...")
                said = True
            time.sleep(2)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default=None,
                    help="input device: a number from --list-devices, or a name such as "
                         "'pipewire' or 'default'")
    ap.add_argument("--list-devices", action="store_true")
    ap.add_argument("--model", default="base.en")
    ap.add_argument("--compute", default="int8", help="int8 on CPU, float16 on GPU")
    ap.add_argument("--cols", type=int, default=20)
    ap.add_argument("--rows", type=int, nargs=2, default=[0, 1],
                    help="which board rows the transcript owns")
    ap.add_argument("--no-board", action="store_true", help="print only, don't send")
    ap.add_argument("--always", action="store_true",
                    help="listen all the time instead of waiting for the show (testing)")
    ap.add_argument("--board", action="store_true",
                    help="write to the board directly, bypassing the show (testing only)")
    ap.add_argument("--rate", type=int, default=None,
                    help="capture rate; default is whatever the device prefers")
    a = ap.parse_args()

    import sounddevice as sd
    if a.list_devices:
        print(sd.query_devices())
        return
    if a.device is not None and str(a.device).strip().lstrip("-").isdigit():
        a.device = int(a.device)          # an index; anything else is a device name

    # USB adapters often refuse 16 kHz, so capture at the device's own rate and
    # resample for Whisper, which always wants 16 kHz mono
    dev_info = sd.query_devices(a.device, "input") if a.device is not None \
        else sd.query_devices(kind="input")
    cap_sr = a.rate or int(dev_info["default_samplerate"])
    print(f"[stt] capturing from {dev_info['name']!r} at {cap_sr} Hz")

    from faster_whisper import WhisperModel
    print(f"[stt] loading {a.model} ...")
    model = WhisperModel(a.model, device="cpu", compute_type=a.compute)

    sock = None if a.no_board else connect_waiting()
    blocks = queue.Queue()

    # The show switches the microphone on only at the interaction points. Until it
    # says so, audio is discarded. --always listens regardless (testing).
    mic = {"on": a.always or sock is None}

    def follow_show():
        """Read events from the renderer; also keeps its queue for us drained."""
        buf = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except OSError:
                chunk = None
            if not chunk:
                # the renderer went away; exit so the service manager restarts us
                # against the new one, rather than sitting on a dead socket
                print("[stt] renderer disconnected; exiting")
                os._exit(3)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("event") == "stt" and not a.always:
                    was, mic["on"] = mic["on"], bool(ev.get("on"))
                    if was != mic["on"]:
                        print(f"[stt] microphone {'on' if mic['on'] else 'off'}")
    if sock is not None:
        import threading
        threading.Thread(target=follow_show, daemon=True).start()

    def cb(indata, _frames, _t, status):
        if status:
            print(f"[stt] {status}", file=sys.stderr)
        blocks.put(indata[:, 0].copy())

    print("[stt] ready; " + ("listening" if mic["on"] else "waiting for the show to open the mic")
          + ". Ctrl-C to stop.")
    buf, speaking, quiet_for, started = [], False, 0.0, 0.0
    with sd.InputStream(samplerate=cap_sr, channels=1, dtype="float32",
                        blocksize=int(cap_sr * BLOCK), device=a.device, callback=cb):
        while True:
            block = blocks.get()
            if not mic["on"]:
                buf, speaking = [], False       # closed: drop anything half-heard
                continue
            level = float(np.sqrt(np.mean(block ** 2)))
            loud = level > LEVEL_GATE

            if loud and not speaking:
                speaking, buf, quiet_for, started = True, [block], 0.0, time.monotonic()
                continue
            if not speaking:
                continue

            buf.append(block)
            quiet_for = 0.0 if loud else quiet_for + BLOCK
            long_enough = len(buf) * BLOCK >= MIN_SPEECH
            done = (quiet_for >= SILENCE_HOLD) or (time.monotonic() - started > MAX_UTTERANCE)
            if not done:
                continue

            speaking = False
            if not long_enough:
                continue

            audio = np.concatenate(buf)
            if cap_sr != SR:                      # linear resample to 16 kHz
                n_out = int(round(len(audio) * SR / cap_sr))
                audio = np.interp(np.linspace(0, len(audio) - 1, n_out),
                                  np.arange(len(audio)), audio).astype(np.float32)
            segments, _info = model.transcribe(audio, language="en", beam_size=1,
                                               vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text:
                continue
            lines = wrap(text, a.cols, 2)
            print(f"[stt] {text!r} -> {lines}")
            # the show decides whether this reaches the board: it only does while a
            # listen step is waiting, so speech during a clip is ignored
            if a.board:
                send(sock, {"cmd": "board",
                            "rows": {str(a.rows[0]): {"text": lines[0], "align": "center"},
                                     str(a.rows[1]): {"text": lines[1], "align": "center"}}})
            send(sock, {"cmd": "utterance", "text": text})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
