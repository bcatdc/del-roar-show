"""
clips.py - clip queue on one persistent libmpv instance.

Every clip from puppet.py starts and ends on the same rest frame. The next clip
is always queued in mpv's playlist ahead of time, so when one ends the next is
already buffered and starts on the following frame; the shared rest frame makes
the join invisible.

Each clip may have a sidecar timeline next to it, e.g. line04.board.json:

  {"cues": [
     {"t": 0.0,  "id": "intro",      "lines": ["", "LIFT THE HANDSET"]},
     {"t": 6.4,  "id": "stem_dread", "lines": ["I STAY UP AT NIGHT", "WORRYING ABOUT THE"]},
     {"t": 14.0, "id": "clear",      "lines": ["", ""]}
  ]}

Cues fire against mpv's own playback position, so the board stays in step with
the voice even if a frame is dropped.
"""
import json
import os
import queue


def _find_libmpv():
    """Windows: libmpv-2.dll sits next to these scripts, not in site-packages.
    ctypes needs an absolute path entry, so add this folder before importing mpv."""
    if os.name == "nt":
        here = os.path.dirname(os.path.abspath(__file__))
        os.environ["PATH"] = here + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory") and os.path.isdir(here):
            try:
                os.add_dll_directory(here)
            except OSError:
                pass


class Player:
    """One persistent libmpv instance. Whatever should play next is always queued
    in mpv's own playlist, so mpv opens and buffers it ahead of time and a clip
    ending flows straight into the next with no held frame. A cut ('now', or a
    switch out of an idle loop) replaces the playlist instead."""

    def __init__(self, clip_dir, ext=".mp4", audio_device="auto", hwdec="auto-safe",
                 volume=100, log=print):
        _find_libmpv()
        import mpv                                 # python-mpv; pip install mpv
        self.clip_dir, self.ext, self.log = clip_dir, ext, log
        self.mpv = mpv.MPV(vo="libmpv", keep_open="yes", keep_open_pause="yes",
                           prefetch_playlist="yes",
                           # keep the audio output open between files, so moving
                           # from one clip to the next never waits for it to drain
                           gapless_audio="yes",
                           hwdec=hwdec, audio_device=audio_device, volume=volume,
                           audio_fallback_to_null="yes", cache="yes", demuxer_max_bytes="64MiB",
                           terminal="no")
        self._init_state()

        @self.mpv.property_observer("eof-reached")
        def _eof(_name, value):
            if value:
                self.events.put(("eof", None))

        @self.mpv.property_observer("time-pos")
        def _pos_cb(_name, value):
            if value is not None:
                self._pos = value

        @self.mpv.event_callback("file-loaded")
        def _loaded(_event):
            self.events.put(("loaded", None))

    def _init_state(self):
        from collections import deque
        self.events = queue.Queue()
        self.current = None          # clip currently on screen
        self.pending = []            # clips asked for, not yet started
        self.loop_clip = None        # clip (or list of clips) looped when nothing is pending
        self._loop_i = 0
        self._queued = deque()       # names sitting in mpv's playlist after the current one
        self._expect = None          # name of a cut that is loading
        self._timeline = []
        self._fired = set()
        self._pos = 0.0
        self._at_eof = False

    # ---------------- talking to mpv ----------------
    def _cmd(self, *args):
        """Every call made during playback is asynchronous. This thread also drives
        mpv's render API, and a blocking call made from it can wait on a frame that
        this same thread is supposed to be drawing - which hangs the renderer."""
        try:
            self.mpv.command_async(*[str(a) for a in args], callback=lambda err, res: None)
        except Exception as e:
            self.events.put(("error", f"mpv {args[0]}: {e}"))

    # ---------------- paths ----------------
    def path(self, clip):
        return clip if os.path.isabs(clip) else os.path.join(self.clip_dir, clip + self.ext)

    def timeline_path(self, clip):
        return os.path.splitext(self.path(clip))[0] + ".board.json"

    # ---------------- what comes next ----------------
    def _peek(self):
        if self.pending:
            return self.pending[0]
        if isinstance(self.loop_clip, (list, tuple)):
            return self.loop_clip[self._loop_i % len(self.loop_clip)] if self.loop_clip else None
        return self.loop_clip

    def _consume(self, name):
        if self.pending and self.pending[0] == name:
            self.pending.pop(0)
        elif isinstance(self.loop_clip, (list, tuple)) and name in self.loop_clip:
            self._loop_i += 1

    @property
    def is_looping(self):
        if self.current is None or self.pending:
            return False
        if isinstance(self.loop_clip, (list, tuple)):
            return self.current in self.loop_clip
        return self.current == self.loop_clip

    # ---------------- control ----------------
    def play(self, clip, then=None, now=False):
        """Queue a clip. It starts at the end of the current clip, or at once if
        the current clip is an idle loop or now=True."""
        was_looping = self.is_looping
        self.pending = [clip]
        if then is not None:
            self.loop_clip, self._loop_i = then, 0
        if now or self.current is None or self._at_eof or was_looping:
            self._cut()
        else:
            self._requeue()

    def loop(self, clip, now=False):
        """clip may be one name or a list; a list cycles."""
        self.loop_clip, self._loop_i = clip, 0
        self.pending = []
        if now or self.current is None or self._at_eof:
            self._cut()
        else:
            self._requeue()

    def stop(self):
        self.pending, self.loop_clip = [], None
        self._requeue()                           # nothing queued: hold the rest frame

    # ---------------- mpv playlist ----------------
    def _exists(self, name):
        if os.path.isfile(self.path(name)):
            return True
        self.events.put(("error", f"clip not found: {self.path(name)}"))
        return False

    def _cut(self):
        """Replace whatever is playing with the next clip, now."""
        name = self._peek()
        if name is None or not self._exists(name):
            return
        self._consume(name)
        self._queued.clear()
        self._expect = name
        self._cmd("loadfile", self.path(name), "replace")
        self._cmd("set", "pause", "no")

    def _append_next(self):
        """Put the follow-on clip in mpv's playlist so it is buffered in advance."""
        if self._queued:
            return
        name = self._peek()
        if name is None or not self._exists(name):
            return
        self._cmd("loadfile", self.path(name), "append")
        self._queued.append(name)

    def _requeue(self):
        """What comes next changed: drop the queued entry and queue the new one."""
        self._cmd("playlist-clear")              # removes everything but the current file
        self._queued.clear()
        self._append_next()

    def _trim_playlist(self):
        """After mpv moves on by itself, the clip that just ended is entry 0; drop it
        so the playlist doesn't grow all show. (After a cut the playlist is already
        just the current clip.) Tracked here rather than read back from mpv, since
        reading a property is a blocking call."""
        self._cmd("playlist-remove", 0)

    def _load_timeline(self, clip):
        tp = self.timeline_path(clip)
        if not os.path.isfile(tp):
            return []
        try:
            with open(tp) as f:
                cues = json.load(f).get("cues", [])
            return sorted(cues, key=lambda c: c["t"])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            self.events.put(("error", f"bad timeline {tp}: {e}"))
            return []

    # ---------------- per-frame ----------------
    def update(self):
        """Returns (events, cues) to act on this frame."""
        out, cues = [], []
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "loaded":
                advanced = self._expect is None
                if not advanced:                        # a cut arrived
                    name, self._expect = self._expect, None
                else:                                   # mpv moved on by itself
                    name = self._queued.popleft() if self._queued else self.current
                    if self.current is not None:
                        out.append({"event": "ended", "clip": self.current})
                    self._consume(name)
                self.current = name
                self._at_eof = False
                self._pos = 0.0
                self._fired.clear()
                self._timeline = self._load_timeline(name)
                out.append({"event": "started", "clip": name})
                if advanced:
                    self._trim_playlist()
                self._append_next()
            elif kind == "eof":
                # only reached when nothing was queued: hold the rest frame
                self._at_eof = True
                if self.current is not None:
                    out.append({"event": "ended", "clip": self.current})
                if self._peek() is not None:
                    self._cut()
            elif kind == "error":
                out.append({"event": "error", "message": payload})

        for i, cue in enumerate(self._timeline):
            if i not in self._fired and self._pos >= cue["t"]:
                self._fired.add(i)
                cues.append(cue)
        return out, cues

    @property
    def position(self):
        return self._pos

    def shutdown(self):
        try:
            self.mpv.terminate()
        except Exception:
            pass
