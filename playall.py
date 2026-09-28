#!/usr/bin/env python3
"""
playall.py - watch a rough cut: play clips back to back through the renderer.

  python3 playall.py                          every clip in clips/, alphabetically
  python3 playall.py intro prompt1 refrain    just these, in this order
  python3 playall.py --gap idle --hold 2      loop idle for 2s between clips
  python3 playall.py --loop                   start over when it reaches the end

The renderer switches at clip boundaries, which is where the shared rest frame
is, so what you are watching is the real transition behaviour and not a preview
of it. Ctrl-C to stop.
"""
import argparse
import glob
import json
import os
import socket
import sys
import time


def connect(path="/tmp/delroar-renderer.sock", port=8765):
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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="*", help="clip names in order; default: all of clips/")
    ap.add_argument("--dir", default="clips")
    ap.add_argument("--gap", default="idle", help="clip to loop between them ('' for none)")
    ap.add_argument("--hold", type=float, default=1.5, help="seconds on the gap clip")
    ap.add_argument("--loop", action="store_true", help="repeat the whole sequence")
    a = ap.parse_args()

    names = a.clips or sorted(
        os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(a.dir, "*.mp4"))
        if os.path.splitext(os.path.basename(p))[0] != a.gap)
    if not names:
        sys.exit(f"no clips found in {a.dir}/")

    try:
        sock = connect()
    except OSError:
        sys.exit("No renderer listening. Start renderer.py first.")

    def send(obj):
        sock.sendall((json.dumps(obj) + "\n").encode())

    print("sequence: " + " -> ".join(names))
    buf = b""
    i = 0
    send({"cmd": "play", "clip": names[0], "then": a.gap or names[0], "now": True})
    print(f"  playing {names[0]}")
    started = time.monotonic()

    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event") == "error":
                print("  ! " + ev.get("message", ""))
            if ev.get("event") != "ended" or ev.get("clip") != names[i]:
                continue
            print(f"    ended after {time.monotonic() - started:.1f}s")
            i += 1
            if i >= len(names):
                if not a.loop:
                    print("done; leaving the gap clip looping")
                    return
                i = 0
            if a.gap and a.hold > 0:
                time.sleep(a.hold)
            send({"cmd": "play", "clip": names[i], "then": a.gap or names[i]})
            print(f"  playing {names[i]}")
            started = time.monotonic()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
