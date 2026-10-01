#!/usr/bin/env python3
"""
renderer.py - Del Roar screen renderer for the Linux NUC.

One fullscreen OpenGL window that draws two things:
  - the pre-rendered puppet clips, played gaplessly through libmpv
  - a split-flap board across the top, flipping to live or timeline strings

It takes orders from show logic over a local socket (see control.py) and knows
nothing about the piece itself.

  python3 renderer.py                          fullscreen on the NUC
  python3 renderer.py --windowed --scale 0.45  smaller window while developing
  python3 renderer.py --no-video               board only, no mpv needed
  python3 renderer.py --demo                   board only, cycling test strings

Requires: python3 -m pip install pygame-ce moderngl mpv
           plus libmpv itself: sudo apt install libmpv2   (or libmpv1)
All of those are free and open source.
"""
import argparse
import ctypes
import ctypes.util
import json
import math
import os
import time

import numpy as np
import moderngl
import pygame
import pygame.freetype

from board import Board, CHARSET, NCHAR, BLOCK
from control import ControlServer, DEFAULT_SOCKET

# ---------------------------------------------------------------------------
# SCREEN
SCREEN_W, SCREEN_H = 1080, 1920        # the composition, always portrait
ROTATE = 0                             # 0 = the display is already portrait;
                                       # 90 or 270 = rotate in software, for a
                                       # landscape display with the panel turned
FPS = 60

# BOARD placement
COLS, ROWS = 20, 3
BOARD_WIDTH = 0.80                     # fraction of screen width
BOARD_TOP = 0.02                       # fraction of screen height above the board
ALIGN = "center"                       # center | left | right

# BOARD look (cell proportions and gaps are in cell widths, as in flapboard.html)
CELL_H = 1.56
GAP_X = 0.09
GAP_Y = 0.24
HINGE = 0.012                          # hinge slot as a fraction of cell height
CONDENSE = 0.80                        # horizontal squeeze on the glyphs
GLYPH_FILL = 0.62                      # cap height as a fraction of cell height
PALETTE = "carnival"                   # carnival | classic | amber | paper | signal

PALETTES = {                           # card face gradient + ink
    # white enamel cards, black ink - the fairground sign look
    "carnival": dict(top=(0xfb, 0xfa, 0xf6), mid=(0xf2, 0xf0, 0xe9), bot=(0xdf, 0xdc, 0xd2),
                     ink=(0x14, 0x13, 0x11)),
    "classic": dict(top=(0x26, 0x28, 0x2c), mid=(0x1a, 0x1c, 0x1f), bot=(0x13, 0x14, 0x17),
                    ink=(0xf5, 0xf0, 0xe4)),
    "amber":   dict(top=(0x1b, 0x18, 0x15), mid=(0x11, 0x10, 0x10), bot=(0x0a, 0x09, 0x09),
                    ink=(0xff, 0xb5, 0x45)),
    "paper":   dict(top=(0xef, 0xe9, 0xdb), mid=(0xe2, 0xdb, 0xca), bot=(0xd2, 0xca, 0xb7),
                    ink=(0x16, 0x17, 0x1a)),
    "signal":  dict(top=(0x14, 0x20, 0x1a), mid=(0x0d, 0x17, 0x12), bot=(0x08, 0x0f, 0x0b),
                    ink=(0x8f, 0xf0, 0xa8)),
}
FONT_CANDIDATES = ["Liberation Sans Narrow", "DejaVu Sans Condensed", "Arial Narrow",
                   "Roboto Condensed", "Liberation Sans", "DejaVu Sans"]

# VIDEO
CLIP_DIR = "clips"
CLIP_EXT = ".mp4"
IDLE_CLIP = "idle"                     # looped whenever nothing else is queued
VIDEO_FIT = "cover"                    # cover = fill the screen, board overlays the top
                                       # below = fit the video in the space under the board
VIDEO_SHIFT_Y = 0                      # px to move the picture up (negative) or down
VIDEO_SHIFT_X = 0                      # px to move it left (negative) or right
AUDIO_DEVICE = "auto"                  # voice, i.e. the clips: "alsa/plughw:CARD=Handset,DEV=0"

# AUDIO BED - music and vamps, played independently of the video
AUDIO_DIR = "audio"
AUDIO_EXT = ".wav"
BED_DEVICE = "auto"                    # sounddevice output: "auto" = system default,
                                       # or an index / name from audiotest.py --list-devices.
                                       # On the NUC, "pipewire" lets the bed and the clips'
                                       # voice share one device without fighting over it
BED_VOLUME = 90
BED_DUCK = 0.25                        # bed level while a clip with voice is playing
BED_DUCK_TIME = 0.35
HWDEC = "auto-safe"
VOLUME = 100

# KNOB (rotary switch, and the handset, wired through a Makey Makey)
# Makey Makey holds a key down while its contact is closed, so these are read as
# states, not keystrokes. Change the keys to match how you wired the pads.
# key: (input name, state while the contact is CLOSED, state while it is OPEN).
# The third value is optional; it defaults to "none" for the selector and "down"
# for anything else. A handset that closes its contact in the cradle, rather than
# when lifted, is ("handset", "down", "up") - the states simply swap.
INPUTS = {
    "space": ("handset", "down", "up"),   # cradle contact: closed at rest, open when lifted
    "left":  ("selector", "left"),
    "right": ("selector", "right"),
}
DEBOUNCE = 0.05            # seconds a contact must hold before it counts
TOGGLE_INPUTS = {"handset"}  # with --toggle-keys, these flip on each press rather
                             # than reporting held state; for testing without the
                             # Makey Makey, whose contacts hold themselves
KNOB_ROW = 2               # which board row the knob owns
KNOB_STEPS = 7             # meter cells to fill before the action fires
KNOB_PER = 0.15            # seconds per further meter cell; the first fills the
                           # instant the knob turns, so 7 cells commit in about 0.9s
KNOB_DETENT_CYCLE = 2.5    # seconds between the two hints while centred

# FLAP SOUND
FLAP_SOUND = True
FLAP_DEVICE = None                     # None = default device; or an SDL device name string
FLAP_VOLUME = 0.55                     # master gain, as in flapboard.html
FLAP_VOICES = 12                       # simultaneous clicks before they start cutting each other

# TICKET ON SCREEN - when local.json says TICKET_MODE screen or both, or a print
# fails, show.py sends the ticket image and it is laid over the picture here
TICKET_WIDTH = 0.70                    # fraction of screen width
TICKET_CENTRE_Y = 0.57                 # where its middle sits, as a fraction of height
TICKET_MAX_H = 0.72                    # never taller than this fraction of height
TICKET_PAPER = (0.97, 0.94, 0.86)      # white becomes this: paper, not a lit screen
TICKET_DIM = 0.55                      # how far the picture behind darkens
TICKET_FADE = 0.6                      # seconds in and out

# BOARD TEXTURE detail
TILE_W, TILE_H = 192, 300              # glyph atlas tile; 2x the on-screen cell is plenty
ATLAS_COLS = 8


# ---------------------------------------------------------------------------
# glyph atlas
def build_atlas(ctx):
    """One texture holding every character in CHARSET, ink on transparent."""
    pygame.freetype.init()
    font = None
    for name in FONT_CANDIDATES:
        path = pygame.freetype.match_font(name, bold=True) or pygame.freetype.match_font(name)
        if path:
            font = pygame.freetype.Font(path, 0)
            break
    if font is None:
        font = pygame.freetype.SysFont(None, 0)
    font.strong = True

    rows = (NCHAR + ATLAS_COLS - 1) // ATLAS_COLS
    sheet = pygame.Surface((ATLAS_COLS * TILE_W, rows * TILE_H), pygame.SRCALPHA)
    cap = int(TILE_H * GLYPH_FILL)
    font.size = cap * 1.35                       # freetype size is em, not cap height

    for i, ch in enumerate(CHARSET):
        if ch == " ":
            continue
        surf, rect = font.render(ch, (255, 255, 255, 255))
        w = max(1, int(rect.width * CONDENSE))
        surf = pygame.transform.smoothscale(surf, (w, rect.height))
        tx = (i % ATLAS_COLS) * TILE_W + (TILE_W - w) // 2
        ty = (i // ATLAS_COLS) * TILE_H + (TILE_H - rect.height) // 2
        sheet.blit(surf, (tx, ty))

    to_bytes = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
    data = to_bytes(sheet, "RGBA", True)
    tex = ctx.texture(sheet.get_size(), 4, data)
    tex.build_mipmaps()
    tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
    tex.anisotropy = 8.0
    return tex, rows


def glyph_uv(idx, atlas_rows, v0=0.0, v1=1.0):
    """UV rect for a glyph, optionally just its top or bottom half.
    The atlas was uploaded flipped, so v is measured from the tile's bottom."""
    c, r = idx % ATLAS_COLS, idx // ATLAS_COLS
    u0 = c / ATLAS_COLS
    u1 = (c + 1) / ATLAS_COLS
    top = 1.0 - r / atlas_rows
    bot = 1.0 - (r + 1) / atlas_rows
    return u0, u1, bot + (top - bot) * (1 - v1), bot + (top - bot) * (1 - v0)


# ---------------------------------------------------------------------------
# quad helpers: everything is drawn as screen-space quads with a shade factor
class QuadBatch:
    def __init__(self, stride=5):
        self.v = []
        self.stride = stride

    def add(self, x0, y0, x1, y1, uv=(0, 0, 0, 0), shade=1.0, tint=None):
        u0, u1, v0, v1 = uv
        t = tint or (1.0, 1.0, 1.0)
        corners = [(x0, y0, u0, v1), (x1, y0, u1, v1), (x1, y1, u1, v0),
                   (x0, y0, u0, v1), (x1, y1, u1, v0), (x0, y1, u0, v0)]
        for x, y, u, vv in corners:
            self.v += [x, y, u, vv, shade, *t]

    def array(self):
        return np.array(self.v, dtype="f4")

    def clear(self):
        self.v.clear()


VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
in float in_shade;
in vec3 in_tint;
out vec2 uv;
out float shade;
out vec3 tint;
uniform vec2 screen;
void main() {
    uv = in_uv; shade = in_shade; tint = in_tint;
    vec2 p = vec2(in_pos.x / screen.x * 2.0 - 1.0, 1.0 - in_pos.y / screen.y * 2.0);
    gl_Position = vec4(p, 0.0, 1.0);
}
"""

FRAG = """
#version 330
in vec2 uv;
in float shade;
in vec3 tint;
out vec4 frag;
uniform sampler2D tex;
uniform int textured;
void main() {
    if (textured == 1) {
        float a = texture(tex, uv).a;
        frag = vec4(tint * shade, a);
    } else {
        frag = vec4(tint * shade, 1.0);
    }
}
"""

ROTATE_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 uv;
void main() { uv = in_uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""


VIDEO_VERT = """
#version 330
in vec2 in_pos;
in vec2 in_uv;
out vec2 uv;
void main() { uv = in_uv; gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

VIDEO_FRAG = """
#version 330
in vec2 uv;
out vec4 frag;
uniform sampler2D tex;
void main() { frag = texture(tex, uv); }
"""


TICKET_FRAG = """
#version 330
in vec2 uv;
out vec4 frag;
uniform sampler2D tex;
uniform vec3 paper;
uniform float alpha;
void main() { frag = vec4(texture(tex, uv).rrr * paper, alpha); }
"""

# the dim layer has no texture, so no UVs at all: a shader that declares an input
# it never uses may have it compiled away, and then the attribute can't be bound
DIM_VERT = """
#version 330
in vec2 in_pos;
void main() { gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

DIM_FRAG = """
#version 330
out vec4 frag;
uniform float alpha;
void main() { frag = vec4(0.0, 0.0, 0.0, alpha); }
"""


def ticket_rect(comp, img_w, img_h):
    """Where the ticket goes in the composition, in pixels: x0, y0, x1, y1."""
    cw, ch = comp
    w = cw * TICKET_WIDTH
    h = w * img_h / img_w
    if h > ch * TICKET_MAX_H:
        h = ch * TICKET_MAX_H
        w = h * img_w / img_h
    cy = ch * TICKET_CENTRE_Y
    y0 = min(max(cy - h / 2, 0), ch - h)
    x0 = (cw - w) / 2
    return x0, y0, x0 + w, y0 + h


class TicketOverlay:
    """The Bill of Exchange shown on screen, over a dimmed picture. It fades in,
    stays for the hold, and fades out; a new visitor's handset clears it early."""

    def __init__(self, ctx, comp):
        self.ctx, self.comp = ctx, comp
        self.prog = ctx.program(vertex_shader=VIDEO_VERT, fragment_shader=TICKET_FRAG)
        self.dim_prog = ctx.program(vertex_shader=DIM_VERT, fragment_shader=DIM_FRAG)
        full = np.array([-1, 1, 1, 1, 1, -1, -1, 1, 1, -1, -1, -1], dtype="f4")
        self.dim_vao = ctx.vertex_array(
            self.dim_prog, [(ctx.buffer(full), "2f", "in_pos")])
        self.tex = self.vao = None
        self.alpha, self.target, self.until = 0.0, 0.0, None

    @staticmethod
    def _load(path):
        """Pixels bottom row first, as OpenGL wants them."""
        try:
            from PIL import Image
            img = Image.open(path).convert("L").transpose(Image.FLIP_TOP_BOTTOM)
            return img.size, 1, img.tobytes()
        except ImportError:
            surf = pygame.image.load(path)
            to = getattr(pygame.image, "tobytes", None) or pygame.image.tostring
            return surf.get_size(), 3, to(surf, "RGB", True)

    def show(self, path, hold=None):
        (w, h), comps, data = self._load(path)
        if self.tex is not None:
            self.tex.release()
        self.tex = self.ctx.texture((w, h), comps, data)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        x0, y0, x1, y1 = ticket_rect(self.comp, w, h)
        cw, ch = self.comp
        X0, X1 = x0 / cw * 2 - 1, x1 / cw * 2 - 1
        Y0, Y1 = 1 - y0 / ch * 2, 1 - y1 / ch * 2
        quad = np.array([X0, Y0, 0, 1, X1, Y0, 1, 1, X1, Y1, 1, 0,
                         X0, Y0, 0, 1, X1, Y1, 1, 0, X0, Y1, 0, 0], dtype="f4")
        if self.vao is not None:
            self.vao.release()
        self.vao = self.ctx.vertex_array(self.prog, [(self.ctx.buffer(quad), "2f 2f",
                                                      "in_pos", "in_uv")])
        self.target = 1.0
        self.until = time.monotonic() + float(hold) if hold else None

    def hide(self):
        self.target, self.until = 0.0, None

    def update(self, dt):
        if self.until is not None and time.monotonic() >= self.until:
            self.hide()
        step = dt / TICKET_FADE if TICKET_FADE > 0 else 1.0
        if self.alpha < self.target:
            self.alpha = min(self.target, self.alpha + step)
        elif self.alpha > self.target:
            self.alpha = max(self.target, self.alpha - step)

    def draw(self):
        if self.alpha <= 0 or self.vao is None:
            return
        self.ctx.enable(moderngl.BLEND)
        self.dim_prog["alpha"].value = self.alpha * TICKET_DIM
        self.dim_vao.render(moderngl.TRIANGLES)
        self.tex.use(0)
        self.prog["tex"].value = 0
        self.prog["paper"].value = TICKET_PAPER
        self.prog["alpha"].value = self.alpha
        self.vao.render(moderngl.TRIANGLES)


SHADE_DEPTH = 0.45 if PALETTE not in ("paper", "carnival") else 0.22


class FlapSound:
    """The click from flapboard.html, ported as-is: band-passed noise, a random
    pitch and band per click, each click offset by up to 14 ms so a line landing
    is a scattered flurry, and quiet - 0.16 / sqrt(voices) under a 0.55 master.
    The Web Audio graph is rendered ahead of time into a bank of variants; each
    frame mixes its clicks into one short buffer."""

    SR = 44100
    VARIANTS = 32

    def __init__(self, volume=FLAP_VOLUME, device=FLAP_DEVICE, voices=FLAP_VOICES):
        self.bank, self.chan = [], 0
        try:
            pygame.mixer.quit()
            kw = dict(frequency=self.SR, size=-16, channels=1, buffer=256)
            if device:
                kw["devicename"] = device
            pygame.mixer.init(**kw)
            pygame.mixer.set_num_channels(voices)
            self.stereo = pygame.mixer.get_init()[2] > 1
        except pygame.error as e:
            print(f"[renderer] no flap sound: {e}")
            return
        self.master = volume
        self.rng = np.random.default_rng(2244)
        sr = self.SR
        n = int(sr * 0.12)
        noise = (self.rng.random(n) * 2 - 1) * (1 - np.arange(n) / n)   # as in the HTML
        t = np.arange(int(sr * 0.08)) / sr
        # envelope: 0.0001 -> 1 over 2 ms, then -> 0.0001 by 50 ms, exponential
        env = np.where(t < 0.002, 0.0001 * (1 / 0.0001) ** (t / 0.002),
                       1.0 * (0.0001) ** ((t - 0.002) / 0.048))
        env[t > 0.05] = 0.0
        for _ in range(self.VARIANTS):
            rate = 0.85 + self.rng.random() * 0.4                   # playbackRate
            src = np.interp(np.arange(0, n, rate), np.arange(n), noise)[:len(t)]
            src = np.pad(src, (0, max(0, len(t) - len(src))))
            f0 = 1500 + self.rng.random() * 1600                    # bandpass centre
            self.bank.append(self._bandpass(src, f0, 1.1, sr) * env)

    @staticmethod
    def _bandpass(x, f0, q, sr):
        """RBJ band-pass, constant 0 dB peak - the same filter Web Audio uses."""
        w0 = 2 * math.pi * f0 / sr
        alpha = math.sin(w0) / (2 * q)
        b0, b2 = alpha, -alpha
        a0, a1, a2 = 1 + alpha, -2 * math.cos(w0), 1 - alpha
        b0, b2, a1, a2 = b0 / a0, b2 / a0, a1 / a0, a2 / a0
        y = np.zeros_like(x)
        x1 = x2 = y1 = y2 = 0.0
        for i, xi in enumerate(x):
            yi = b0 * xi + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, xi, y1, yi
            y[i] = yi
        return y

    def play(self, clicks):
        if not self.bank or clicks <= 0:
            return
        voices = min(clicks, 10)
        vol = min(0.5, 0.16 / math.sqrt(voices)) * (1.4 if clicks > 6 else 1.0)
        span = int(self.SR * 0.014)
        out = np.zeros(len(self.bank[0]) + span)
        for _ in range(voices):
            off = int(self.rng.random() * span)
            v = self.bank[int(self.rng.integers(len(self.bank)))]
            out[off:off + len(v)] += v * vol
        out *= self.master
        samples = (np.clip(out, -1, 1) * 32767).astype(np.int16)
        if self.stereo:
            samples = np.repeat(samples[:, None], 2, axis=1)
        snd = pygame.sndarray.make_sound(np.ascontiguousarray(samples))
        pygame.mixer.Channel(self.chan % pygame.mixer.get_num_channels()).play(snd)
        self.chan += 1


class Knob:
    """The rotary switch's row: two hints while centred, and a filling meter
    toward whichever label the knob is turned to. Timing lives here rather than
    in show logic, because this process already owns the frame clock."""

    def __init__(self, board, row=KNOB_ROW):
        self.board, self.row = board, row
        self.armed = False
        self.left = self.right = ""
        self.steps, self.per = KNOB_STEPS, KNOB_PER
        self.side = None
        self.filled = 0
        self.t = 0.0
        self.hint = 0
        self.fired = False
        self._restore = False      # put the full drum back once the row settles

    def _options(self):
        """Every string this row can show, so its drums stay short."""
        opts = [("< " + self.left, "left"), (self.right + " >", "right")]
        for f in range(self.steps + 1):
            opts.append((self.left + " " + BLOCK * f, "left"))
            opts.append((BLOCK * f + " " + self.right, "right"))
        return opts

    def arm(self, left, right, steps=None, per=None):
        self.left, self.right = left.upper(), right.upper()
        self.steps = steps or KNOB_STEPS
        self.per = per or KNOB_PER
        self.armed, self.fired, self._restore = True, False, False
        self.side, self.filled, self.t, self.hint = None, 0, 0.0, 0
        self.board.set_row_drums(self.row, self._options())
        self._show_hint()

    def disarm(self, clear=True):
        self.armed = False
        if clear:
            self.board.clear_row(self.row)       # one flip, while the drum is short
            self._restore = True                 # full drum returns once it lands
        else:
            self.board.set_row_drums(self.row, None)

    def _show_hint(self):
        if self.hint % 2 == 0:
            self.board.set_row(self.row, "< " + self.left, "left")
        else:
            self.board.set_row(self.row, self.right + " >", "right")

    def _show_meter(self):
        bar = BLOCK * self.filled
        if self.side == "left":
            self.board.set_row(self.row, self.left + " " + bar, "left")
        else:
            self.board.set_row(self.row, bar + " " + self.right, "right")

    def set_side(self, side):
        """side is 'left', 'right' or None (knob centred)."""
        if not self.armed or side == self.side:
            return []
        self.side, self.t, self.fired = side, 0.0, False
        if side is None:
            self.filled = 0
            self.hint = 0
            self._show_hint()
            return [{"event": "knob", "side": None, "filled": 0}]
        # the first cell lands the moment the knob turns, so it feels attached to the hand
        self.filled = 1
        self._show_meter()
        return [{"event": "knob", "side": side, "filled": 1}]

    def update(self, dt):
        if self._restore and self.board.settled(self.row):
            self.board.set_row_drums(self.row, None)
            self._restore = False
        if not self.armed:
            return []
        out = []
        self.t += dt
        if self.side is None:
            if self.t >= KNOB_DETENT_CYCLE:
                self.t = 0.0
                self.hint += 1
                self._show_hint()
            return out
        if self.fired:
            return out
        if self.t >= self.per:
            self.t = 0.0
            self.filled += 1
            self._show_meter()
            out.append({"event": "knob", "side": self.side, "filled": self.filled})
            if self.filled >= self.steps:
                self.fired = True
                out.append({"event": "activated", "side": self.side,
                            "label": self.left if self.side == "left" else self.right})
        return out


class Inputs:
    """Makey Makey contacts read as held keys, debounced, reported as states."""

    def __init__(self, mapping=INPUTS, toggle=False):
        self.toggle = toggle
        self._was = {}
        self.mapping = {}
        for key, spec in mapping.items():
            if isinstance(spec, str) or len(spec) < 2:
                print(f"[renderer] INPUTS['{key}'] should be [name, closed, open?]")
                continue
            name, closed = spec[0], spec[1]
            opened = spec[2] if len(spec) > 2 else ("none" if name == "selector" else "down")
            # pygame names letters lowercase (K_w) and named keys uppercase (K_SPACE)
            code = next((getattr(pygame, f"K_{form}")
                         for form in (str(key), str(key).lower(), str(key).upper())
                         if hasattr(pygame, f"K_{form}")), None)
            if code is None:
                print(f"[renderer] unknown key in INPUTS: {key}")
                continue
            self.mapping[code] = (name, closed, opened)
        self.state = {}            # name -> current state
        self._since = {}           # code -> how long its reading has held

    def update(self, dt):
        pressed = pygame.key.get_pressed()
        if self.toggle:
            return self._update_toggle(pressed)
        raw, idle = {}, {}
        for code, (name, closed, opened) in self.mapping.items():
            idle[name] = opened
            held = pressed[code]
            key = (code, held)
            self._since[key] = self._since.get(key, 0.0) + dt
            if self._since[key] < DEBOUNCE:
                continue
            if held:
                raw[name] = closed
        for name, opened in idle.items():
            raw.setdefault(name, opened)

        out = []
        for name, state in raw.items():
            if self.state.get(name) != state:
                self.state[name] = state
                out.append({"event": "input", "name": name, "state": state})
        return out

    def _update_toggle(self, pressed):
        """Testing mode: a tap flips the handset instead of having to hold the key.
        Held inputs (the knob) are resolved across ALL their keys first, so A not
        being pressed can't cancel D being pressed in the same frame."""
        out = []
        held_state = {}
        for code, (name, closed, opened) in self.mapping.items():
            held, was = pressed[code], self._was.get(code, False)
            self._was[code] = held
            if name in TOGGLE_INPUTS:
                if held and not was:                   # press edge only
                    now = opened if self.state.get(name) == closed else closed
                    self.state[name] = now
                    out.append({"event": "input", "name": name, "state": now})
                continue
            held_state.setdefault(name, opened)
            if held:
                held_state[name] = closed
        for name, state in held_state.items():
            if self.state.get(name) != state:
                self.state[name] = state
                out.append({"event": "input", "name": name, "state": state})
        return out


class BoardRenderer:
    """Draws the board's cells as flat quads with a hinge shadow and a falling
    flap that foreshortens as it swings. Same state machine and timing as the
    three.js version; the 3D is faked, which at this tile size reads the same."""

    def __init__(self, ctx, board, screen):
        self.ctx, self.board = ctx, board
        self.screen = screen
        self.pal = PALETTES[PALETTE]
        self.atlas, self.atlas_rows = build_atlas(ctx)
        self.prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        self.prog["screen"].value = screen
        self.buf = ctx.buffer(reserve=COLS * ROWS * 6 * 6 * 8 * 4, dynamic=True)
        self.vao = ctx.vertex_array(
            self.prog, [(self.buf, "2f 2f 1f 3f", "in_pos", "in_uv", "in_shade", "in_tint")])
        self.layout()

    def layout(self):
        w = self.screen[0] * BOARD_WIDTH
        self.cw = w / (COLS + (COLS - 1) * GAP_X)
        self.ch = self.cw * CELL_H
        self.gx = self.cw * GAP_X
        self.gy = self.cw * GAP_Y
        self.x0 = (self.screen[0] - w) / 2
        self.y0 = self.screen[1] * BOARD_TOP
        self.height = ROWS * self.ch + (ROWS - 1) * self.gy

    def cell_rect(self, col, row):
        x = self.x0 + col * (self.cw + self.gx)
        y = self.y0 + row * (self.ch + self.gy)
        return x, y, x + self.cw, y + self.ch

    def draw(self):
        pal = self.pal
        top = tuple(c / 255 for c in pal["top"])
        mid = tuple(c / 255 for c in pal["mid"])
        bot = tuple(c / 255 for c in pal["bot"])
        ink = tuple(c / 255 for c in pal["ink"])

        cards, glyphs = QuadBatch(), QuadBatch()
        hinge = self.ch * HINGE

        for col, row, top_glyph, bot_glyph, flap in self.board.faces():
            x0, y0, x1, y1 = self.cell_rect(col, row)
            ym = (y0 + y1) / 2

            # fixed halves, with the hinge slot between them
            cards.add(x0, y0, x1, ym - hinge, tint=top)
            cards.add(x0, ym + hinge, x1, y1, tint=bot)
            if top_glyph:
                glyphs.add(x0, y0, x1, ym, glyph_uv(top_glyph, self.atlas_rows, 0.0, 0.5),
                           tint=ink)
            if bot_glyph:
                glyphs.add(x0, ym, x1, y1, glyph_uv(bot_glyph, self.atlas_rows, 0.5, 1.0),
                           tint=ink)

            if flap is None:
                continue
            glyph, angle, showing_back = flap
            f = abs(math.cos(angle))                     # foreshortening
            lit = 1.0 - SHADE_DEPTH * math.sin(angle)    # face turns away from the light
            if not showing_back:
                fy0, fy1 = ym - (ym - y0) * f, ym
                uv_glyph = glyph_uv(glyph, self.atlas_rows, 0.0, 0.5)
                tint_card = mid
            else:
                fy0, fy1 = ym, ym + (y1 - ym) * f
                # the back of the flap carries the incoming bottom half, and it
                # lands upright, so it needs no flip - only the foreshortening
                uv_glyph = glyph_uv(glyph, self.atlas_rows, 0.5, 1.0)
                tint_card = mid
            cards.add(x0, fy0, x1, fy1, shade=lit, tint=tint_card)
            glyphs.add(x0, fy0, x1, fy1, uv_glyph, shade=lit, tint=ink)
            # shadow the half the flap is falling onto
            if not showing_back:
                cards.add(x0, ym + hinge, x1, y1,
                          shade=(1 - SHADE_DEPTH) + SHADE_DEPTH * (1 - math.sin(angle)),
                          tint=bot)

        self.ctx.enable(moderngl.BLEND)
        for batch, textured in ((cards, 0), (glyphs, 1)):
            data = batch.array()
            if not len(data):
                continue
            if data.nbytes > self.buf.size:
                self.buf.orphan(data.nbytes)
            self.buf.write(data)
            self.prog["textured"].value = textured
            if textured:
                self.atlas.use(0)
                self.prog["tex"].value = 0
            self.vao.render(moderngl.TRIANGLES, vertices=len(data) // 8)



def gl_proc_loader():
    """Give libmpv a way to look up OpenGL functions.

    This has to resolve against the SAME GL context pygame created, so candidates
    are tried in order and each is tested on a real function before being used:
      1. SDL already loaded in this process (RTLD_DEFAULT), which is always right
      2. SDL by filename, for Windows where the DLL is loaded per-module
      3. EGL, which is what KMSDRM and Wayland use
      4. GLX, for a classic X session
      5. wglGetProcAddress plus opengl32, for Windows without SDL
    """
    import glob
    candidates = []

    def sdl_from(lib):
        lib.SDL_GL_GetProcAddress.restype = ctypes.c_void_p
        lib.SDL_GL_GetProcAddress.argtypes = [ctypes.c_char_p]
        return lambda _ctx, name: lib.SDL_GL_GetProcAddress(name)

    # 1. whatever SDL pygame itself loaded (POSIX only: Windows has no
    #    "this process" library handle, so it goes straight to candidate 2)
    if os.name != "nt":
        try:
            this_process = ctypes.CDLL(None)
            this_process.SDL_GL_GetProcAddress      # raises if the symbol isn't there
            candidates.append(("sdl-in-process", sdl_from(this_process)))
        except (OSError, AttributeError, TypeError):
            pass

    # 2. SDL by filename (Windows, and as a fallback elsewhere)
    base = os.path.dirname(os.path.abspath(pygame.__file__))
    paths = []
    for pattern in ("SDL2.dll", "SDL3.dll", "libSDL2*.so*", "libSDL2*.dylib"):
        paths += glob.glob(os.path.join(base, pattern))
        paths += glob.glob(os.path.join(base, ".libs", pattern))
    found = ctypes.util.find_library("SDL2") or ctypes.util.find_library("SDL2-2.0")
    if found:
        paths.append(found)
    for p in paths:
        try:
            candidates.append((f"sdl:{os.path.basename(p)}", sdl_from(ctypes.CDLL(p))))
        except OSError:
            pass

    # 3. EGL - KMSDRM and Wayland
    for name in ("libEGL.so.1", "libEGL.so"):
        try:
            egl = ctypes.CDLL(name)
            egl.eglGetProcAddress.restype = ctypes.c_void_p
            egl.eglGetProcAddress.argtypes = [ctypes.c_char_p]
            candidates.append(("egl", lambda _ctx, n, egl=egl: egl.eglGetProcAddress(n)))
            break
        except OSError:
            pass

    # 4. GLX - a classic X session
    try:
        gl = ctypes.CDLL("libGL.so.1")
        gl.glXGetProcAddressARB.restype = ctypes.c_void_p
        gl.glXGetProcAddressARB.argtypes = [ctypes.c_char_p]
        candidates.append(("glx", lambda _ctx, n: gl.glXGetProcAddressARB(n)))
    except (OSError, AttributeError):
        pass

    # 5. Windows without SDL
    if os.name == "nt":
        try:
            gl32 = ctypes.WinDLL("opengl32")
            gl32.wglGetProcAddress.restype = ctypes.c_void_p
            gl32.wglGetProcAddress.argtypes = [ctypes.c_char_p]
            k32 = ctypes.WinDLL("kernel32")
            k32.GetModuleHandleW.restype = ctypes.c_void_p
            k32.GetProcAddress.restype = ctypes.c_void_p
            k32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            handle = k32.GetModuleHandleW(ctypes.c_wchar_p("opengl32.dll"))

            def wgl(_ctx, name):
                addr = gl32.wglGetProcAddress(name)
                if not addr or addr in (1, 2, 3, 0xFFFFFFFFFFFFFFFF):
                    addr = k32.GetProcAddress(handle, name)
                return addr
            candidates.append(("wgl", wgl))
        except (OSError, AttributeError):
            pass

    # test each one on functions libmpv will actually ask for
    for label, load in candidates:
        try:
            if all(load(None, fn) for fn in (b"glGetString", b"glActiveTexture", b"glBindFramebuffer")):
                print(f"[renderer] GL functions via {label}")
                return load
        except Exception:
            continue
    raise RuntimeError("could not find a way to look up OpenGL functions; "
                       f"tried: {', '.join(l for l, _ in candidates) or 'nothing'}")


GRADE_FRAG = """
#version 330
in vec2 uv;
out vec4 frag;
uniform sampler2D tex;
uniform float gain;     // overall brightness
uniform vec3 tint;      // multiplies each channel, e.g. a cold blue for attract
uniform float lift;     // pulls the blacks up toward the tint, like light in a dark room
void main() {
    vec4 c = texture(tex, uv);
    vec3 graded = c.rgb * tint * gain + tint * lift;
    frag = vec4(graded, c.a);
}
"""

LIGHT_PRESETS = {
    # gain, tint (r, g, b), lift
    "attract": (0.38, (0.62, 0.72, 1.00), 0.015),
    "on":      (1.00, (1.00, 1.00, 1.00), 0.0),
    "dim":     (0.65, (0.90, 0.92, 1.00), 0.0),
}


class Light:
    """The room light on Del Roar, driven by show logic. Moves smoothly between
    presets, or flickers like a filament catching before it settles."""

    def __init__(self, preset="on"):
        g, t, l = LIGHT_PRESETS[preset]
        self.cur = [g, *t, l]
        self.frm = list(self.cur)
        self.to = list(self.cur)
        self.dur = 0.0                     # nothing to move toward yet
        self.t = 0.0
        self.flicker = []            # (start, end, level 0..1) pulses before settling

    def set(self, preset=None, gain=None, tint=None, lift=None, over=0.6, flicker=False,
            rng=None):
        g, t, l = LIGHT_PRESETS.get(preset, LIGHT_PRESETS["on"]) if preset else (None, None, None)
        g = gain if gain is not None else (g if g is not None else self.to[0])
        t = tint if tint is not None else (t if t is not None else self.to[1:4])
        l = lift if lift is not None else (l if l is not None else self.to[4])
        self.frm, self.to = list(self.cur), [g, *t, l]
        self.dur, self.t = max(over, 1e-3), 0.0
        self.flicker = []
        if flicker:
            rng = rng or np.random.default_rng()
            at, pulses = 0.0, int(rng.integers(3, 6))
            for i in range(pulses):
                on = rng.uniform(0.03, 0.09) * (1 + i * 0.4)      # catches, longer each time
                off = rng.uniform(0.04, 0.14) * (1 - i / (pulses + 1))
                self.flicker.append((at, at + on, rng.uniform(0.6, 1.0)))
                at += on + off
            self.dur = max(self.dur, at + over)
            self._flicker_end = at

    def update(self, dt):
        if self.dur <= 0:                  # already where it was asked to be
            self.cur = list(self.to)
            return
        self.t += dt
        if self.flicker:
            if self.t < self._flicker_end:
                level = next((lv for s, e, lv in self.flicker if s <= self.t < e), 0.0)
                self.cur = [a + (b - a) * level for a, b in zip(self.frm, self.to)]
                return
            u = min(1.0, (self.t - self._flicker_end) / max(self.dur - self._flicker_end, 1e-3))
        else:
            u = min(1.0, self.t / self.dur)
        e = 0.5 - 0.5 * math.cos(math.pi * u)
        self.cur = [a + (b - a) * e for a, b in zip(self.frm, self.to)]

    def apply(self, prog):
        prog["gain"].value = float(self.cur[0])
        prog["tint"].value = tuple(float(x) for x in self.cur[1:4])
        prog["lift"].value = float(self.cur[4])


class VideoLayer:
    """libmpv rendering into our own framebuffer, drawn as a screen quad."""

    def __init__(self, ctx, player, screen, rect):
        import mpv
        self.ctx, self.player, self.rect = ctx, player, rect
        w, h = int(rect[2] - rect[0]), int(rect[3] - rect[1])
        self.tex = ctx.texture((w, h), 4)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.fbo = ctx.framebuffer(color_attachments=[self.tex])
        self.size = (w, h)

        # python-mpv renamed this type: 1.0.x uses MpvGlGetProcAddressFn,
        # older releases called it OpenGlCbGetProcAddrFn
        fn_type = getattr(mpv, "MpvGlGetProcAddressFn", None) or mpv.OpenGlCbGetProcAddrFn
        self._proc = fn_type(gl_proc_loader())
        self.mpv_ctx = mpv.MpvRenderContext(
            player.mpv, "opengl", opengl_init_params={"get_proc_address": self._proc})

        # screen quad for the video, in clip space
        x0 = rect[0] / screen[0] * 2 - 1
        x1 = rect[2] / screen[0] * 2 - 1
        y0 = 1 - rect[1] / screen[1] * 2
        y1 = 1 - rect[3] / screen[1] * 2
        quad = np.array([x0, y0, 0, 1, x1, y0, 1, 1, x1, y1, 1, 0,
                         x0, y0, 0, 1, x1, y1, 1, 0, x0, y1, 0, 0], dtype="f4")
        self.prog = ctx.program(vertex_shader=VIDEO_VERT, fragment_shader=GRADE_FRAG)
        self.vao = ctx.vertex_array(self.prog, [(ctx.buffer(quad), "2f 2f", "in_pos", "in_uv")])
        self.light = None

    def render_into_fbo(self):
        self.mpv_ctx.render(flip_y=True, opengl_fbo={"w": self.size[0], "h": self.size[1],
                                                     "fbo": self.fbo.glo})

    def draw(self):
        self.tex.use(0)
        self.prog["tex"].value = 0
        if self.light is not None:
            self.light.apply(self.prog)
        else:
            self.prog["gain"].value, self.prog["tint"].value, self.prog["lift"].value = \
                1.0, (1.0, 1.0, 1.0), 0.0
        self.vao.render(moderngl.TRIANGLES)

    def shutdown(self):
        try:
            self.mpv_ctx.free()
        except Exception:
            pass


class Rotator:
    """Draws the portrait composition onto a landscape display, turned 90 or 270
    degrees. Everything else in this file can go on working in portrait."""

    def __init__(self, ctx, comp_size, screen_size, degrees):
        self.ctx, self.degrees = ctx, degrees
        self.tex = ctx.texture(comp_size, 4)
        self.tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.fbo = ctx.framebuffer(color_attachments=[self.tex])
        # corners of the screen quad, in clip space, with UVs turned
        # screen corners, counter-clockwise from bottom-left, and where each one
        # reads from in the portrait texture (v runs up, as OpenGL samples it)
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        uvs = {90: [(1, 0), (1, 1), (0, 1), (0, 0)],      # portrait top -> screen right
               270: [(0, 1), (0, 0), (1, 0), (1, 1)]}[degrees % 360]
        order = [0, 1, 2, 0, 2, 3]
        data = []
        for i in order:
            data += [*corners[i], *uvs[i]]
        self.prog = ctx.program(vertex_shader=ROTATE_VERT, fragment_shader=VIDEO_FRAG)
        self.vao = ctx.vertex_array(
            self.prog, [(ctx.buffer(np.array(data, dtype="f4")), "2f 2f", "in_pos", "in_uv")])

    def use(self):
        self.fbo.use()

    def blit(self):
        self.ctx.screen.use()
        self.ctx.clear(0.0, 0.0, 0.0)
        self.tex.use(0)
        self.prog["tex"].value = 0
        self.vao.render(moderngl.TRIANGLES)


def video_rect(board_bottom):
    if VIDEO_FIT == "below":
        h = SCREEN_H - board_bottom
        w = h * SCREEN_W / SCREEN_H
        x = (SCREEN_W - w) / 2
        rect = (x, board_bottom, x + w, SCREEN_H)
    else:
        rect = (0, 0, SCREEN_W, SCREEN_H)
    dx, dy = VIDEO_SHIFT_X, VIDEO_SHIFT_Y
    return (rect[0] + dx, rect[1] + dy, rect[2] + dx, rect[3] + dy)


SHOW_SETTINGS = {"TICKET_MODE", "TICKET_SCREEN_HOLD"}   # in local.json, but read by show.py


def load_local_settings(path="local.json"):
    """Per-machine settings, kept out of version control.

    local.json holds whatever differs between the NUC, the tower and the laptop:

      {"ROTATE": 90, "AUDIO_DEVICE": "pipewire", "BED_DEVICE": "pipewire",
       "VOLUME": 100, "CLIP_DIR": "clips3"}

    Anything named here replaces the default of the same name, so pulling a new
    version of this file never clobbers a machine's own setup."""
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            settings = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"[renderer] ignoring {path}: {e}")
        return {}
    applied = {}
    for key, value in settings.items():
        if key in SHOW_SETTINGS:
            continue                        # show.py's, not ours
        if key.isupper() and key in globals():
            globals()[key] = value
            applied[key] = value
        else:
            print(f"[renderer] {path}: no setting called {key}")
    if applied:
        print("[renderer] local settings: " +
              ", ".join(f"{k}={v!r}" for k, v in sorted(applied.items())))
    return applied


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--windowed", action="store_true")
    ap.add_argument("--scale", type=float, default=1.0, help="window scale when windowed")
    ap.add_argument("--no-video", action="store_true", help="board only; mpv is not started")
    ap.add_argument("--no-bed", action="store_true", help="skip the audio bed player")
    ap.add_argument("--audio", default=AUDIO_DIR)
    ap.add_argument("--demo", action="store_true", help="board only, cycling test strings")
    ap.add_argument("--clips", default=CLIP_DIR)
    ap.add_argument("--socket", default=DEFAULT_SOCKET)
    ap.add_argument("--port", type=int, default=None, help="also listen on this TCP port")
    ap.add_argument("--idle", default=IDLE_CLIP)
    ap.add_argument("--mute", action="store_true", help="no flap clicks")
    ap.add_argument("--light", default="on", choices=sorted(LIGHT_PRESETS),
                    help="lighting preset at startup")
    ap.add_argument("--settings", default="local.json",
                    help="per-machine settings file, kept out of git")
    ap.add_argument("--toggle-keys", action="store_true",
                    help="tap the handset key to lift/hang up instead of holding (testing)")
    ap.add_argument("--rotate", type=int, choices=[0, 90, 270], default=None,
                    help="rotate the whole composition for a landscape display")
    ap.add_argument("--knob-demo", action="store_true",
                    help="board only: a sample transcript with the knob armed")
    a = ap.parse_args()
    local = load_local_settings(a.settings)
    for key, dest in (("CLIP_DIR", "clips"), ("AUDIO_DIR", "audio"), ("IDLE_CLIP", "idle")):
        if key in local and getattr(a, dest) == ap.get_default(dest):
            setattr(a, dest, local[key])   # a flag on the command line still wins

    rotate = ROTATE if a.rotate is None else a.rotate
    pygame.init()
    # ask for a desktop-GL core context: libmpv refuses GLES and pre-3.x contexts,
    # and SDL's KMSDRM path will hand out GLES unless told otherwise
    for attr, value in ((pygame.GL_CONTEXT_MAJOR_VERSION, 3),
                        (pygame.GL_CONTEXT_MINOR_VERSION, 3),
                        (pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE),
                        (pygame.GL_DOUBLEBUFFER, 1)):
        try:
            pygame.display.gl_set_attribute(attr, value)
        except (AttributeError, pygame.error):
            pass
    flags = pygame.OPENGL | pygame.DOUBLEBUF
    size = (SCREEN_H, SCREEN_W) if rotate else (SCREEN_W, SCREEN_H)
    if a.windowed:
        size = (int(size[0] * a.scale), int(size[1] * a.scale))
    else:
        flags |= pygame.FULLSCREEN
    pygame.display.set_mode(size, flags)
    pygame.display.set_caption("Del Roar renderer")
    pygame.mouse.set_visible(False)
    ctx = moderngl.create_context()
    print(f"[renderer] GL {ctx.info['GL_VERSION']} on {ctx.info['GL_RENDERER']}")
    got = pygame.display.get_window_size()
    print(f"[renderer] display {got[0]}x{got[1]}, composing {SCREEN_W}x{SCREEN_H} portrait, "
          f"rotate {rotate}")
    if (got[0] > got[1]) != bool(rotate):
        print("[renderer] NOTE: a portrait display needs ROTATE 0; a landscape one needs 90 "
              "or 270. The picture will look squeezed if this is the wrong way round.")
    ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA

    # the composition is always portrait; Rotator turns it at the end if needed
    comp = (int(SCREEN_W * a.scale), int(SCREEN_H * a.scale)) if a.windowed else \
           (SCREEN_W, SCREEN_H)
    if not rotate:
        comp = size
    rotator = Rotator(ctx, comp, size, rotate) if rotate else None

    board = Board(COLS, ROWS)
    boardr = BoardRenderer(ctx, board, comp)
    boardr.screen = comp
    boardr.prog["screen"].value = comp
    boardr.layout()

    player = video = None
    if not (a.no_video or a.demo):
        from clips import Player
        player = Player(a.clips, CLIP_EXT, AUDIO_DEVICE, HWDEC, VOLUME)
        rect = video_rect(boardr.y0 + boardr.height)
        scale = comp[0] / SCREEN_W
        video = VideoLayer(ctx, player, comp, tuple(v * scale for v in rect))
        player.loop(a.idle)

    light = Light(a.light)
    if video:
        video.light = light
    light_rng = np.random.default_rng(7)

    ticket_layer = TicketOverlay(ctx, comp)
    knob = Knob(board)
    inputs = Inputs(toggle=a.toggle_keys)
    sfx = FlapSound() if (FLAP_SOUND and not a.mute) else None

    bed = None
    if not (a.no_bed or a.demo or a.knob_demo):
        try:
            from audio import Bed
            bed = Bed(a.audio, AUDIO_EXT, BED_DEVICE, BED_VOLUME)
        except Exception as e:          # no sounddevice, no device, bad rate ...
            print(f"[renderer] no audio bed: {e}")

    server = ControlServer(a.socket, a.port)
    where = [server.path] if server.path else []
    if server.port:
        where.append(f"127.0.0.1:{server.port}")
    print("[renderer] listening on " + ", ".join(where))

    if a.knob_demo:
        board.snap_to(["THE LAST TRAIN HOME", "BEFORE THE STORM", ""], align="center")
        board.set_row(2, "", "left")
        knob.arm("SAY IT AGAIN", "LET IT STAND")
        keys = ", ".join(f"{pygame.key.name(c)}={n}" for c, (n, *_r) in inputs.mapping.items())
        print(f"[renderer] knob demo inputs: {keys}")

    clock = pygame.time.Clock()
    hold_until = None
    demo_next = 0.0
    demo_lines = [["LIFT THE HANDSET", ""], ["I CANNOT TRANSMIT", "WHAT IS NOT SPOKEN"],
                  ["A DESIRE", "AND A DREAD"], ["IT IS SPOKEN", "IT IS HEARD"]]
    demo_i = 0
    was_settled = True
    running = True

    while running:
        dt = clock.tick(FPS) / 1000.0
        now = time.monotonic()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_q):
                running = False

        # ---- commands from show logic ----
        for cmd in server.poll():
            kind = cmd.get("cmd")
            try:
                if kind == "board":
                    if "rows" in cmd:                      # per-row: others untouched
                        for key, spec in cmd["rows"].items():
                            if isinstance(spec, str):
                                spec = {"text": spec}
                            board.set_row(int(key), spec.get("text", ""),
                                          spec.get("align"), spec.get("snap", False))
                    else:
                        lines = cmd.get("lines") or [cmd.get("text", "")]
                        (board.snap_to if cmd.get("snap") else board.set_text)(
                            lines, cmd.get("align", ALIGN))
                    hold_until = now + cmd["hold"] if cmd.get("hold") else None
                elif kind == "knob":
                    if cmd.get("off"):
                        knob.disarm(cmd.get("clear", True))
                    else:
                        knob.arm(cmd["left"], cmd["right"], cmd.get("steps"), cmd.get("per"))
                elif kind == "clear":
                    board.clear()
                    hold_until = None
                elif kind == "play" and player:
                    player.play(cmd["clip"], cmd.get("then"), cmd.get("now", False))
                    player.cues_enabled = cmd.get("cues", True)
                elif kind == "loop" and player:
                    player.loop(cmd["clip"], cmd.get("now", False))
                elif kind == "stop" and player:
                    player.stop()
                elif kind == "status":
                    server.emit({"event": "status",
                                 "clip": player.current if player else None,
                                 "pos": round(player.position, 3) if player else None,
                                 "board": board.read_text(),
                                 "inputs": dict(inputs.state),
                                 "knob": {"armed": knob.armed, "side": knob.side,
                                          "filled": knob.filled}})
                elif kind == "audio" and bed:
                    if cmd.get("stop"):
                        ev = bed.stop(cmd.get("fade_out", 0.0))
                    elif "duck" in cmd:
                        bed.duck(cmd["duck"], cmd.get("over", BED_DUCK_TIME))
                        ev = None
                    else:
                        ev = bed.play(cmd["track"], cmd.get("fade_in", 0.0),
                                      cmd.get("loop", True), cmd.get("level", 1.0))
                    if ev:
                        server.emit(ev)
                        if ev.get("event") == "error":
                            print("[renderer] " + ev.get("message", ""))
                elif kind == "light":
                    light.set(cmd.get("preset"), cmd.get("gain"), cmd.get("tint"), cmd.get("lift"),
                              cmd.get("over", 0.6), cmd.get("flicker", False), light_rng)
                elif kind == "stt":
                    # show logic switching the microphone; passed on to stt.py
                    server.emit({"event": "stt", "on": bool(cmd.get("on"))})
                elif kind == "utterance":
                    # the STT process talks to the renderer; pass it on to show logic
                    server.emit({"event": "utterance", "text": cmd.get("text", "")})
                elif kind == "ticket":
                    if cmd.get("off"):
                        ticket_layer.hide()
                    else:
                        try:
                            ticket_layer.show(cmd["path"], cmd.get("hold"))
                        except (OSError, ValueError, pygame.error) as e:
                            server.emit({"event": "error", "message": f"ticket image: {e}"})
                            print(f"[renderer] ticket image: {e}")
                elif kind == "quit":
                    running = False
                else:
                    server.emit({"event": "error", "message": f"unknown cmd: {kind}"})
            except KeyError as e:
                server.emit({"event": "error", "message": f"missing field {e} in {kind}"})

        # ---- clip events and timeline cues ----
        if bed:
            for e in bed.update(dt):
                server.emit(e)
                if e.get("event") == "error":
                    print("[renderer] " + e.get("message", ""))

        if player:
            events, cues = player.update()
            for e in events:
                server.emit(e)
                if e.get("event") == "error":     # say it here too: a client may not be connected
                    print("[renderer] " + e.get("message", ""))
                if bed and BED_DUCK < 1.0 and e.get("clip"):
                    # a clip with voice pulls the bed down; back up when it ends.
                    # the loop may be several idle clips, so check against all of them
                    loop = player.loop_clip
                    idle_set = set(loop) if isinstance(loop, (list, tuple)) else {loop, a.idle}
                    if e["clip"] not in idle_set:
                        if e["event"] == "started":
                            bed.duck(BED_DUCK, BED_DUCK_TIME)
                        elif e["event"] == "ended":
                            bed.duck(1.0, BED_DUCK_TIME * 2)
            for cue in cues:
                if "lines" in cue and getattr(player, "cues_enabled", True):
                    board.set_text(cue["lines"], cue.get("align", ALIGN))
                    hold_until = None
                server.emit({"event": "cue", "clip": player.current, "id": cue.get("id")})

        for e in inputs.update(dt):
            server.emit(e)
            if e["name"] == "selector":
                for k in knob.set_side(None if e["state"] == "none" else e["state"]):
                    server.emit(k)
        for k in knob.update(dt):
            server.emit(k)

        if hold_until and now >= hold_until:
            board.clear()
            hold_until = None

        if a.demo and now >= demo_next:
            board.set_text(demo_lines[demo_i % len(demo_lines)])
            demo_i += 1
            demo_next = now + 5.0

        light.update(dt)
        ticket_layer.update(dt)
        clicks = board.update(dt)
        if sfx:
            sfx.play(clicks)
        if board.settled() and not was_settled:
            server.emit({"event": "settled", "board": board.read_text()})
        was_settled = board.settled()

        # ---- draw ----
        if video:
            video.render_into_fbo()
        (rotator.use() if rotator else ctx.screen.use())
        ctx.clear(0.0, 0.0, 0.0)
        if video:
            video.draw()
        boardr.draw()
        ticket_layer.draw()
        if rotator:
            rotator.blit()
        pygame.display.flip()

    if bed:
        bed.shutdown()
    if video:
        video.shutdown()
    if player:
        player.shutdown()
    pygame.quit()


if __name__ == "__main__":
    main()
