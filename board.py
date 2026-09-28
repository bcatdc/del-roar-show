"""
board.py - split-flap board state machine, ported from flapboard.html.

No graphics here: this module only tracks what each cell shows and where each
flap is in its swing. renderer.py draws it, and this file can be driven
headlessly for tests.

Two things beyond the original:

  * per-row updates, so Whisper can own rows 0 and 1 while the knob owns row 2
  * per-cell drums, so a cell that only ever shows a few characters reaches them
    in a flip or two instead of travelling the whole 56-character drum. Real
    boards do this with special modules; here it is what lets the knob row
    respond instantly.
"""
import math
import random

CHARSET = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:'?!-/&$#@*+%<>\u2588"
CHAR_INDEX = {c: i for i, c in enumerate(CHARSET)}
NCHAR = len(CHARSET)
BLOCK = "\u2588"          # solid cell, for the knob's meter

STEP_DUR = 0.018          # seconds per single flap, before per-cell jitter
                          # (flapboard.html defaulted to 0.062; much faster on purpose)
EASE_POW = 1.75           # flap accelerates as it falls
LEAD_SCALE = 0.5          # how long the cascade takes to sweep across the board
                          # (1.0 = flapboard.html's stagger; lower = the whole line moves sooner)


class Cell:
    __slots__ = ("col", "row", "cur", "nxt", "tgt", "flipping", "t", "wait",
                 "jitter", "lead", "drum")

    def __init__(self, col, row, jitter, lead):
        self.col, self.row = col, row
        self.cur = self.nxt = self.tgt = 0
        self.flipping = False
        self.t = 0.0
        self.wait = 0.0
        self.jitter = jitter
        self.lead = lead
        self.drum = None          # None = the full CHARSET; else a list of indices

    @property
    def angle(self):
        """Flap rotation in radians: 0 = closed/up, pi = fallen."""
        if not self.flipping:
            return 0.0
        p = min(1.0, max(0.0, self.t / (STEP_DUR * self.jitter)))
        return math.pi * (p ** EASE_POW)


class Board:
    def __init__(self, cols=20, rows=3, seed=7):
        self.cols, self.rows = cols, rows
        rng = random.Random(seed)          # fixed seed: same cascade every run
        self.cells = [Cell(c, r, 0.86 + rng.random() * 0.28,
                           (rng.random() * 0.09 + c * 0.0035 + r * 0.012) * LEAD_SCALE)
                      for r in range(rows) for c in range(cols)]
        self.align = ["center"] * rows
        self.clicks = 0                                 # flaps landed last update

    def cell(self, row, col):
        return self.cells[row * self.cols + col]

    # ---------------- text in ----------------
    def sanitize(self, line):
        return "".join(ch if ch in CHAR_INDEX else " " for ch in (line or "").upper())

    def fit(self, text, align="center"):
        s = self.sanitize(text)[:self.cols]
        if align == "center":
            s = " " * ((self.cols - len(s)) // 2) + s
        elif align == "right":
            s = " " * (self.cols - len(s)) + s
        return (s + " " * self.cols)[:self.cols]

    def set_row(self, row, text, align=None, snap=False):
        """Flip one row. Other rows are left exactly as they are."""
        align = align or self.align[row]
        s = self.fit(text, align)
        self.align[row] = align
        for col, ch in enumerate(s):
            tgt = CHAR_INDEX[ch]
            cell = self.cell(row, col)
            if snap:
                cell.cur = cell.tgt = tgt
                cell.flipping = False
                cell.t = cell.wait = 0.0
            elif tgt != cell.tgt:
                cell.tgt = tgt
                if not cell.flipping:
                    cell.wait = cell.lead
        return s

    def set_text(self, lines, align="center", snap=False):
        """Flip every row at once."""
        return [self.set_row(r, lines[r] if r < len(lines) else "", align, snap)
                for r in range(self.rows)]

    def snap_to(self, lines, align="center"):
        return self.set_text(lines, align, snap=True)

    def clear_row(self, row):
        return self.set_row(row, "")

    def clear(self):
        return [self.clear_row(r) for r in range(self.rows)]

    # ---------------- per-cell drums ----------------
    def set_row_drums(self, row, options):
        """Shorten this row's drums to only the characters it can ever show.
        `options` is every string the row might display, each either a plain
        string or (string, align). Pass None to restore the full drum."""
        if options is None:
            for col in range(self.cols):
                self.cell(row, col).drum = None
            return
        padded = [self.fit(*o) if isinstance(o, tuple) else self.fit(o, self.align[row])
                  for o in options]
        for col in range(self.cols):
            chars = {" "} | {p[col] for p in padded}
            # keep CHARSET order, so the flip direction still matches a real drum
            self.cell(row, col).drum = [i for i, ch in enumerate(CHARSET) if ch in chars]

    def steps_to(self, row, col, ch):
        """How many flaps this cell needs to reach ch - handy in tests."""
        cell = self.cell(row, col)
        seq = cell.drum or list(range(NCHAR))
        tgt = CHAR_INDEX[ch]
        if tgt not in seq:
            return None
        here = seq.index(cell.cur) if cell.cur in seq else 0
        return (seq.index(tgt) - here) % len(seq)

    # ---------------- per-frame ----------------
    def update(self, dt):
        self.clicks = 0
        for cell in self.cells:
            if not cell.flipping:
                if cell.cur == cell.tgt:
                    continue
                if cell.wait > 0:
                    cell.wait -= dt
                    if cell.wait > 0:
                        continue
                self._start_step(cell)
                cell.t = 0.0
            dur = STEP_DUR * cell.jitter
            cell.t += dt
            guard = 0
            while cell.flipping and cell.t >= dur and guard < 40:
                guard += 1
                cell.t -= dur
                self._finish_step(cell)
                if cell.cur != cell.tgt:
                    self._start_step(cell)
        return self.clicks

    def _start_step(self, cell):
        seq = cell.drum
        if seq:
            here = seq.index(cell.cur) if cell.cur in seq else -1
            cell.nxt = seq[(here + 1) % len(seq)]
        else:
            cell.nxt = (cell.cur + 1) % NCHAR
        cell.flipping = True

    def _finish_step(self, cell):
        cell.cur = cell.nxt
        cell.flipping = False
        cell.t = 0.0
        self.clicks += 1

    def settled(self, row=None):
        cells = self.cells if row is None else [self.cell(row, c) for c in range(self.cols)]
        return all(not c.flipping and c.cur == c.tgt for c in cells)

    def read_text(self):
        rows = [[" "] * self.cols for _ in range(self.rows)]
        for cell in self.cells:
            rows[cell.row][cell.col] = CHARSET[cell.cur]
        return ["".join(r) for r in rows]

    # ---------------- what to draw ----------------
    def faces(self):
        """Per cell: (col, row, top_glyph, bottom_glyph, flap) where flap is
        None or (glyph, angle, showing_back). renderer.py turns this into quads."""
        out = []
        for cell in self.cells:
            if not cell.flipping:
                out.append((cell.col, cell.row, cell.cur, cell.cur, None))
                continue
            a = cell.angle
            back = a > math.pi / 2
            out.append((cell.col, cell.row, cell.nxt, cell.cur,
                        (cell.cur if not back else cell.nxt, a, back)))
        return out
