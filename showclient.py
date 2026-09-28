#!/usr/bin/env python3
"""
showclient.py - stand-in for show logic, to test the renderer by hand.

  python3 showclient.py                      interactive
  python3 showclient.py board "TWO TRUTHS" "MUST BE SPOKEN"
  python3 showclient.py play line04 idle
  python3 showclient.py watch                just print events

Interactive keys/commands:
  b <text> | <text>    flip the board (| splits rows)
  s <text> | <text>    snap the board, no flapping
  c                    clear the board
  p <clip> [then]      play a clip at the next boundary
  p! <clip> [then]     play it immediately, cutting the current clip
  l <clip>             loop a clip
  x                    stop playback
  ?                    status
  q                    quit this client (renderer keeps running)
"""
import json
import socket
import sys
import threading

SOCKET = "/tmp/delroar-renderer.sock"
PORT = 8765                # used on Windows, or when the renderer was given --port


def connect(path=SOCKET, port=PORT):
    if hasattr(socket, "AF_UNIX"):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
            return s
        except OSError:
            pass                      # no unix socket there; try TCP
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect(("127.0.0.1", port))
    return s


def reader(sock):
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            print("\n[client] renderer closed the connection")
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip():
                print("\n< " + line.decode(), end="\n> ", flush=True)


def send(sock, obj):
    sock.sendall((json.dumps(obj) + "\n").encode())


def split_rows(text):
    return [p.strip() for p in text.split("|")]


def main():
    try:
        sock = connect()
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        sys.exit(f"No renderer listening on {SOCKET} or 127.0.0.1:{PORT}. Start renderer.py first.")
    threading.Thread(target=reader, args=(sock,), daemon=True).start()

    args = sys.argv[1:]
    if args:
        cmd = args[0]
        if cmd == "board":
            send(sock, {"cmd": "board", "lines": args[1:]})
        elif cmd == "play":
            send(sock, {"cmd": "play", "clip": args[1],
                        **({"then": args[2]} if len(args) > 2 else {})})
        elif cmd == "loop":
            send(sock, {"cmd": "loop", "clip": args[1]})
        elif cmd == "clear":
            send(sock, {"cmd": "clear"})
        elif cmd == "watch":
            threading.Event().wait()
        else:
            sys.exit(f"unknown: {cmd}")
        import time
        time.sleep(1.0)          # give events a moment to arrive
        return

    print(__doc__)
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if not line:
            continue
        head, _, rest = line.partition(" ")
        if head == "q":
            return
        elif head == "b":
            send(sock, {"cmd": "board", "lines": split_rows(rest)})
        elif head == "s":
            send(sock, {"cmd": "board", "lines": split_rows(rest), "snap": True})
        elif head == "c":
            send(sock, {"cmd": "clear"})
        elif head in ("p", "p!"):
            parts = rest.split()
            send(sock, {"cmd": "play", "clip": parts[0],
                        **({"then": parts[1]} if len(parts) > 1 else {}),
                        **({"now": True} if head == "p!" else {})})
        elif head == "l":
            send(sock, {"cmd": "loop", "clip": rest.strip()})
        elif head == "x":
            send(sock, {"cmd": "stop"})
        elif head == "?":
            send(sock, {"cmd": "status"})
        else:
            print("unknown command; see the list above")


if __name__ == "__main__":
    main()
