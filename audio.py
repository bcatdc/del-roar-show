"""
audio.py - the audio bed, played independently of the video.

Same engine as audiotest.py, so what you approve there is what the piece does:
one continuous output stream, loops that crossfade into themselves, and fades and
ducks applied per sample. Nothing starts or stops mid-stream, so nothing clicks.

  bed.play("vamp_1")                 # loop it; a crossfade hides the seam
  bed.play("vamp_1")                 # already playing: carries on, no restart
  bed.duck(0.25, 0.3)                # drop under a spoken line
  bed.duck(1.0, 0.6)                 # come back up
  bed.stop(fade_out=0.8)             # dissolve; the video keeps looping

Call update(dt) once a frame for events. Needs sounddevice and ffmpeg.
"""
import os
import subprocess
import threading

import numpy as np

SR = 48000
CH = 2
LOOP_XFADE = 0.25        # seconds of overlap at each loop seam
EDGE = 0.005             # micro-fade on every file's edges, against clicks


def decode(path):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", str(CH), "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True)
    if raw.returncode != 0 or not raw.stdout:
        raise OSError(f"can't decode {path}")
    buf = np.frombuffer(raw.stdout, np.float32).reshape(-1, CH).copy()
    edge = min(int(EDGE * SR), len(buf) // 2)
    if edge:
        ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)[:, None]
        buf[:edge] *= ramp
        buf[-edge:] *= ramp[::-1]
    return buf


class _Voice:
    def __init__(self, buf, fade_in=0):
        self.buf, self.pos = buf, 0
        self.gain = 0.0 if fade_in else 1.0
        self.target = 1.0
        self.step = (1.0 / fade_in) if fade_in else 0.0
        self.handed_over = False

    def fade_to(self, target, samples):
        self.target = target
        self.step = abs(target - self.gain) / max(samples, 1)

    @property
    def remaining(self):
        return len(self.buf) - self.pos

    @property
    def done(self):
        return self.remaining <= 0 or (self.target == 0.0 and self.gain <= 0.0)

    def render(self, n):
        chunk = self.buf[self.pos:self.pos + n]
        k = len(chunk)
        self.pos += k
        if k == 0:
            return chunk
        if self.step == 0:
            return chunk * self.gain
        d = 1.0 if self.target > self.gain else -1.0
        ramp = self.gain + d * self.step * np.arange(1, k + 1, dtype=np.float32)
        ramp = np.minimum(ramp, self.target) if d > 0 else np.maximum(ramp, self.target)
        self.gain = float(ramp[-1])
        return chunk * ramp[:, None]


class Bed:
    def __init__(self, audio_dir, ext=".wav", device="auto", volume=100, sink=None,
                 xfade=LOOP_XFADE):
        """sink=False skips opening a sound card (tests call render() directly)."""
        self.dir, self.ext = audio_dir, ext
        self.xf = int(xfade * SR)
        self.volume = volume / 100.0
        self.lock = threading.Lock()
        self.cache = {}
        self.voices = []
        self.main = None
        self.track = None
        self.loop = True
        self.master, self.m_target, self.m_step = 1.0, 1.0, 0.0
        self.stop_at_zero = False
        self._events = []
        self.stream = None
        if sink is False:
            return
        import sounddevice as sd
        dev = None if device in (None, "", "auto") else (int(device) if str(device).isdigit() else device)
        self.stream = sd.OutputStream(samplerate=SR, channels=CH, dtype="float32", device=dev,
                                      blocksize=512, callback=self._callback)
        self.stream.start()

    def path(self, track):
        return track if os.path.isabs(track) else os.path.join(self.dir, track + self.ext)

    def _buf(self, track):
        if track not in self.cache:
            self.cache[track] = decode(self.path(track))
        return self.cache[track]

    # ---------------- transport ----------------
    def play(self, track, fade_in=0.0, loop=True, level=1.0):
        p = self.path(track)
        if not os.path.isfile(p):
            return {"event": "error", "message": f"audio not found: {p}"}
        buf = self._buf(track)
        with self.lock:
            if track == self.track and not self.stop_at_zero:
                self._ramp_master(level, 0.3)       # already playing: just settle the level
                return {"event": "audio", "track": track, "state": "playing"}
            fin = int(fade_in * SR) if fade_in else (self.xf if self.voices else 0)
            for v in self.voices:                   # any previous bed crosses out
                v.fade_to(0.0, max(fin, 1))
                v.handed_over = True
            self.main = _Voice(buf, fade_in=fin)
            self.voices.append(self.main)
            self.track, self.loop = track, loop
            self.stop_at_zero = False
            if self.master < level or self.m_target < level:
                self._ramp_master(level, 0.05)
        return {"event": "audio", "track": track, "state": "playing"}

    def stop(self, fade_out=0.8):
        with self.lock:
            if self.track is None:
                return None
            self._ramp_master(0.0, fade_out)
            self.stop_at_zero = True
            return {"event": "audio", "track": self.track, "state": "stopping"}

    def duck(self, level, over=0.3):
        with self.lock:
            if self.track is not None and not self.stop_at_zero:
                self._ramp_master(level, over)

    def _ramp_master(self, target, seconds):
        self.m_target = max(0.0, min(1.0, target))
        # never shorter than 5 ms: an instant change mid-waveform clicks
        self.m_step = abs(self.m_target - self.master) / max(int(seconds * SR), int(0.005 * SR))

    # ---------------- mixing ----------------
    def render(self, frames):
        out = np.zeros((frames, CH), np.float32)
        with self.lock:
            filled = 0
            while filled < frames:
                n = frames - filled
                m = self.main
                if m and self.loop and not m.handed_over:
                    if m.remaining <= self.xf + 1:
                        m.handed_over = True
                        m.fade_to(0.0, max(min(self.xf, m.remaining), 1))
                        self.main = _Voice(m.buf, fade_in=self.xf)
                        self.voices.append(self.main)
                    else:
                        n = min(n, m.remaining - self.xf)
                under = np.zeros((n, CH), np.float32)
                for v in list(self.voices):
                    part = v.render(n)
                    under[:len(part)] += part
                    if v.done:
                        self.voices.remove(v)
                if self.m_step:
                    d = 1.0 if self.m_target > self.master else -1.0
                    ramp = self.master + d * self.m_step * np.arange(1, n + 1, dtype=np.float32)
                    ramp = np.minimum(ramp, self.m_target) if d > 0 else np.maximum(ramp, self.m_target)
                    under *= ramp[:, None]
                    self.master = float(ramp[-1])
                    if self.master == self.m_target:
                        self.m_step = 0.0
                else:
                    under *= self.master
                out[filled:filled + n] = under * self.volume
                filled += n

            if self.stop_at_zero and self.master <= 0.0 and self.track is not None:
                self._events.append({"event": "audio", "track": self.track, "state": "stopped"})
                self.voices, self.main, self.track = [], None, None
                self.stop_at_zero = False
                self.master, self.m_target, self.m_step = 1.0, 1.0, 0.0
            elif self.track is not None and not self.voices:
                self._events.append({"event": "audio", "track": self.track, "state": "ended"})
                self.main, self.track = None, None
        np.clip(out, -1.0, 1.0, out=out)
        return out

    def _callback(self, outdata, frames, _time, _status):
        outdata[:] = self.render(frames)

    def update(self, _dt=0.0):
        with self.lock:
            ev, self._events = self._events, []
        return ev

    def shutdown(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
