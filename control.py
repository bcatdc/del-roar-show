"""
control.py - the seam between show logic and this renderer.

A line-delimited JSON socket. Show logic connects, sends commands, and reads
events. The renderer never knows anything about fortunes or the handset.

Commands (show logic -> renderer):
  {"cmd":"play",  "clip":"line04", "then":"idle"}    queue a clip, then idle loop
  {"cmd":"loop",  "clip":"idle"}                     loop until something else is played
  {"cmd":"board", "lines":["FIRST","SECOND"]}        flip the board to this text
  {"cmd":"board", "lines":["..."], "hold":4.0}       ... and clear it after hold seconds
  {"cmd":"board", "lines":["..."], "snap":true}      no flapping, jump straight there
  {"cmd":"clear"}                                    blank the board
  {"cmd":"status"}                                   ask for a status event
  {"cmd":"stop"}                                     stop playback, hold rest frame

Events (renderer -> show logic):
  {"event":"started","clip":"line04"}
  {"event":"ended","clip":"line04"}
  {"event":"cue","clip":"line04","id":"stem_dread"}
  {"event":"settled"}                                board finished flapping
  {"event":"status","clip":"line04","pos":3.2,"board":["...","..."]}
  {"event":"error","message":"..."}
"""
import json
import os
import queue
import socket
import threading

DEFAULT_SOCKET = "/tmp/delroar-renderer.sock"
DEFAULT_PORT = 8765
OUTBOX = 2000              # events held for a client that isn't reading, before dropping
HAS_UNIX = hasattr(socket, "AF_UNIX")     # false on Windows Python


class ControlServer:
    """Accepts several clients at once. Commands land in .commands; events are
    broadcast to everyone connected."""

    def __init__(self, path=DEFAULT_SOCKET, tcp_port=None):
        self.commands = queue.Queue()
        self._clients = []
        self._outbox = {}          # conn -> queue of events waiting to be sent
        self._lock = threading.Lock()
        self._socks = []

        if path and not HAS_UNIX:
            path = None                   # Windows: fall back to TCP on localhost
            tcp_port = tcp_port or DEFAULT_PORT
        self.path, self.port = path, tcp_port

        if path:
            if os.path.exists(path):
                os.unlink(path)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(path)
            os.chmod(path, 0o666)
            s.listen(8)
            self._socks.append(s)
        if tcp_port:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", tcp_port))
            s.listen(8)
            self._socks.append(s)

        for s in self._socks:
            threading.Thread(target=self._accept, args=(s,), daemon=True).start()

    def _accept(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            out = queue.Queue(maxsize=OUTBOX)
            with self._lock:
                self._clients.append(conn)
                self._outbox[conn] = out
            threading.Thread(target=self._read, args=(conn,), daemon=True).start()
            threading.Thread(target=self._write, args=(conn, out), daemon=True).start()

    def _read(self, conn):
        buf = b""
        with conn:
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.commands.put(json.loads(line))
                    except json.JSONDecodeError as e:
                        self._send(conn, {"event": "error", "message": f"bad JSON: {e}"})
        self._drop(conn)

    def _drop(self, conn):
        with self._lock:
            if conn in self._clients:
                self._clients.remove(conn)
            out = self._outbox.pop(conn, None)
        if out is not None:
            try:
                out.put_nowait(None)                 # tells the writer thread to stop
            except queue.Full:
                pass

    def _write(self, conn, out):
        """Each client has its own sender, so a client that is slow, or never reads
        at all, can only ever block this thread - never the renderer."""
        while True:
            obj = out.get()
            if obj is None:
                return
            try:
                conn.sendall((json.dumps(obj) + "\n").encode())
            except OSError:
                self._drop(conn)
                return

    def _send(self, conn, obj):
        with self._lock:
            out = self._outbox.get(conn)
        if out is None:
            return
        try:
            out.put_nowait(obj)
        except queue.Full:
            # this client isn't reading; drop the event rather than wait for it
            pass

    def emit(self, obj):
        """Never blocks: events go into each client's own queue."""
        with self._lock:
            clients = list(self._clients)
        for conn in clients:
            self._send(conn, obj)

    def poll(self):
        """Every command waiting right now."""
        out = []
        while True:
            try:
                out.append(self.commands.get_nowait())
            except queue.Empty:
                return out
