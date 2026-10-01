#!/usr/bin/env python3
"""
ticket_art.py - draw the Bill of Exchange as an image, for printing as a raster.

The text version of the ticket is a receipt. This one is a ticket: square, the
fortune set full width and large enough to be the object on the page, a heavy
border with corner blocks, paired rules, and a small lion between the two holder
lines. Borders and rules are drawn here rather than supplied as art, so they stay
sharp at any size.

  python3 ticket_art.py --demo                 write ticket.png and look at it
  python3 ticket_art.py --demo --print --printer usb:0x154f:0x154f

Fonts go in fonts/ (Rye for the title, League Gothic for everything else; both
free from fonts.google.com and theleagueofmoveabletype.com). Art goes in art/:
lion.png, a black-on-white stamp. Anything missing falls back to a system font
or is left out, so this runs before the assets exist.

Needs Pillow (pip install pillow).
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw, ImageFont

DPI = 203
WIDTH = 576                # dots the ticket itself is drawn at (72mm)
PRINTABLE = 640            # dots the head can lay down: the BK-C310 self-test
                           # reports a maximum print width of 80mm, which is 640
LEFT_MARGIN = 40            # dots to shift right; ~8 per mm, so 40 for the 5mm walk
RATIO = 1.25                # height as a multiple of width; 1.0 is square
PAD = 26                   # inside the border
BORDER = 10                # outer rule
INNER = 0                  # inner rule
GAP = 0                    # between the two
CORNER = 10                # solid corner blocks
NOTCH = 1                # white square cut from the inner face of each block

LION = "art/lion.png"
CREDIT = "art/credit.png"  # a small colophon below the frame; left out if absent
CREDIT_W = 0.95            # its width, as a fraction of the ticket
CREDIT_PAD = 36            # white above the credit
CREDIT_TAIL = 0           # white below it, before the cutter's own feed
UPSIDE_DOWN = False        # print rotated 180 degrees, for a printer mounted so the
                           # ticket comes out towards the visitor the wrong way up
RULE_STYLE = "segments"       # the rule above the holder row:
                           #   solid     edge to edge
                           #   broken    a gap in the middle, over the lion
                           #   segments  one short rule over each column
FONT_DIR = "fonts"

FALLBACKS = ["/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
             "C:/Windows/Fonts/arialbd.ttf"]


def find_font(*wanted, folder=FONT_DIR):
    """Find a font file by fragments of its name, anywhere under fonts/. Families
    arrive with their own folder layouts (static/, variable/, differing hyphens),
    so match loosely rather than demanding an exact path."""
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if not name.lower().endswith((".ttf", ".otf")):
                continue
            flat = name.lower().replace("_", "").replace("-", "").replace(" ", "")
            if all(w.lower().replace(" ", "") in flat for w in wanted):
                return os.path.join(root, name)
    return None


TITLE_FONT = find_font("rye")                      # the Tuscan circus title
SANS_FONT = find_font("leaguegothic") or find_font("oswald")
CLERK_FONT = find_font("stint") or SANS_FONT       # the small clerical lines
BODY_FONT = SANS_FONT                              # the fortune itself


def font(path, size):
    for candidate in [path] + FALLBACKS:
        if candidate and os.path.isfile(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size)


def text_size(draw, text, f):
    box = draw.textbbox((0, 0), text, font=f)
    return box[2] - box[0], box[3] - box[1]


def fit_line(draw, text, path, width, max_size=200, min_size=8):
    """Largest size at which one line fits the width."""
    for size in range(max_size, min_size - 1, -2):
        f = font(path, size)
        if text_size(draw, text, f)[0] <= width:
            return f
    return font(path, min_size)


def wrap_to(draw, text, f, width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if text_size(draw, trial, f)[0] <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def line_height(f, gap=0.92):
    """From the font's own metrics: a sample string misses descenders and leading."""
    ascent, descent = f.getmetrics()
    return (ascent + descent) * gap


def wood_type(draw, text, path, width, height, max_size=200, min_lines=2, max_lines=8):
    """Set the fortune the way a jobbing printer would: break it into lines, then
    size EACH line to fill the measure. That is what makes the lines different
    sizes and the block solid, rather than one size ragged down the page."""
    words = text.split()
    best = None
    for count in range(min_lines, min(max_lines, len(words)) + 1):
        lines = balance(words, count)
        sized, total = [], 0
        for line in lines:
            f = fit_line(draw, line, path, width, max_size=max_size)
            h = line_height(f, 0.86)
            sized.append((line, f, h))
            total += h
        if total <= height:
            if best is None or total > best[1]:      # the fullest that still fits
                best = (sized, total)
    if best is None:                                 # nothing fits: shrink to fit
        f = font(path, 14)
        lines = wrap_to(draw, text, f, width)
        return [(ln, f, line_height(f)) for ln in lines]
    return best[0]


def balance(words, count):
    """Split words into `count` lines of roughly equal character length, so no
    line is left with one short word."""
    target = sum(len(w) + 1 for w in words) / count
    lines, current, used = [], [], 0
    for word in words:
        if current and used + len(word) + 1 > target and len(lines) < count - 1:
            lines.append(" ".join(current))
            current, used = [word], len(word)
        else:
            current.append(word)
            used += len(word) + 1
    if current:
        lines.append(" ".join(current))
    while len(lines) < count:                        # too few: split the longest
        longest = max(range(len(lines)), key=lambda i: len(lines[i]))
        parts = lines[longest].split()
        if len(parts) < 2:
            break
        half = len(parts) // 2
        lines[longest:longest + 1] = [" ".join(parts[:half]), " ".join(parts[half:])]
    return lines


def fit_block(draw, text, path, width, height, max_size=120):
    """Largest size at which the wrapped text fills the width and fits the height.
    A four-word fortune and a twelve-word one both end up filling the same block,
    which is what stops the layout looking empty or cramped."""
    for size in range(max_size, 11, -2):
        f = font(path, size)
        lines = wrap_to(draw, text, f, width)
        line_h = line_height(f)
        if len(lines) * line_h <= height and \
                all(text_size(draw, ln, f)[0] <= width for ln in lines):
            return f, lines, line_h
    f = font(path, 12)
    return f, wrap_to(draw, text, f, width), line_height(f)


def rule(draw, x0, x1, y, weights=(5, 2), gap=5, style="solid", middle=0):
    """A paired rule: thick then thin, the Victorian jobbing printer's habit.
    style "broken" leaves a gap of `middle` dots in the centre; "segments" draws
    one short rule over each of the two columns instead of one long one."""
    for w in weights:
        if style == "broken" and middle:
            half = (x1 - x0 - middle) / 2
            draw.rectangle([x0, y, x0 + half, y + w - 1], fill=0)
            draw.rectangle([x1 - half, y, x1, y + w - 1], fill=0)
        elif style == "segments" and middle:
            half = (x1 - x0 - middle) / 2
            for sx in (x0, x1 - half):
                draw.rectangle([sx + half * 0.15, y, sx + half * 0.85, y + w - 1], fill=0)
        else:
            draw.rectangle([x0, y, x1, y + w - 1], fill=0)
        y += w + gap
    return y


def stamp(path, box):
    """Load a piece of art and reduce it to pure black and white. Anything grey
    at 203dpi turns to mud, so everything is thresholded rather than dithered."""
    if not os.path.isfile(path):
        return None
    art = Image.open(path).convert("L")
    art.thumbnail((box, box), Image.LANCZOS)
    return art.point(lambda v: 0 if v < 128 else 255, mode="1")


def draw_ticket(fortune, bearer=None, prior=None, title="BILL OF EXCHANGE",
                subtitle="THE FANTABULOUS DEL ROAR \u2014 FORTUNE EXCHANGER",
                fine="ALL FORTUNES FINAL.", serial=None, width=WIDTH,
                ratio=RATIO, lion=LION, credit=CREDIT, margin=None, flip=None):
    """ratio is height as a multiple of width: 1.0 square, 1.25 a little taller.
    Past about 1.6 it starts reading as a receipt again.

    margin shifts the ticket right to cope with paper walk. It is added to the
    width while the total fits the head's PRINTABLE dots, and only eats into the
    ticket beyond that."""
    margin = LEFT_MARGIN if margin is None else margin
    if width + margin > PRINTABLE:
        width = PRINTABLE - margin          # too wide for the head: give up ticket
    sheet_w = width + margin
    height = int(width * ratio)
    img = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(img)

    # frame: thick outer rule, a white channel, a hairline inner rule, and solid
    # corner blocks notched out of the channel - the carnival ticket's signature
    d.rectangle([0, 0, width - 1, height - 1], outline=0, width=BORDER)
    inset = BORDER + GAP
    d.rectangle([inset, inset, width - 1 - inset, height - 1 - inset], outline=0, width=INNER)
    for cx, cy in ((0, 0), (width - CORNER, 0), (0, height - CORNER),
                   (width - CORNER, height - CORNER)):
        d.rectangle([cx, cy, cx + CORNER - 1, cy + CORNER - 1], fill=0)
        nx = cx + (CORNER - NOTCH) if cx == 0 else cx
        ny = cy + (CORNER - NOTCH) if cy == 0 else cy
        d.rectangle([nx, ny, nx + NOTCH - 1, ny + NOTCH - 1], fill=255)

    x0 = inset + PAD
    x1 = width - 1 - inset - PAD
    inner_w = x1 - x0
    y = inset + PAD - 4

    # title, as large as it will go
    tf = fit_line(d, title, TITLE_FONT, inner_w, max_size=int(width * 0.16))
    tw, th = text_size(d, title, tf)
    d.text(((width - tw) / 2, y), title, font=tf, fill=0)
    y += th + 16

    # subtitle
    sf = fit_line(d, subtitle, SANS_FONT, inner_w, max_size=int(width * 0.062))
    sw, sh = text_size(d, subtitle, sf)
    d.text(((width - sw) / 2, y), subtitle, font=sf, fill=0)
    y += sh + 14
    y = rule(d, x0, x1, y)

    # what the bottom block needs, so the fortune can have the rest
    # two columns either side of the lion, each broken after its colon:
    #   BEARER:              PRIOR HOLDER:
    #   BEN, 43                 INES, 52
    lion_w = int(width * 0.22) if os.path.isfile(lion) else 0
    column = (inner_w - lion_w - 40) / 2 if lion_w else inner_w / 2 - 20
    columns = []
    for text in (bearer, prior):
        if not text:
            columns.append(None)
            continue
        label, _, value = text.partition(":")
        columns.append((label.strip() + ":", value.strip()))
    widest = max((part for col in columns if col for part in col), key=len, default="BEARER:")
    small = fit_line(d, widest, CLERK_FONT, column, max_size=int(width * 0.062))
    small_h = line_height(small, 1.0)
    fine_f = fit_line(d, fine or "X", CLERK_FONT, inner_w * 0.9,
                      max_size=int(width * 0.052))
    fine_h = line_height(fine_f, 1.0)
    lion_img = stamp(lion, lion_w) if lion_w else None
    holder_h = max(small_h * 2 + 6, lion_img.height if lion_img else 0)
    bottom = holder_h + fine_h + 52
    block_h = height - inset - PAD - bottom - y

    set_lines = wood_type(d, fortune.upper(), BODY_FONT, inner_w, block_h,
                          max_size=int(width * 0.30))
    # a line's size is limited by the measure, not the height, so on a taller
    # ticket there is space left over: spread it between the lines as leading
    # rather than leaving a gap above and below
    used = sum(h for _t, _f, h in set_lines)
    slack = max(0, block_h - used)
    lead = min(slack / max(len(set_lines), 1), max(h for _t, _f, h in set_lines) * 0.35)
    ty = y + max(0, (block_h - used - lead * (len(set_lines) - 1)) / 2)
    for i, (line, lf, lh) in enumerate(set_lines):
        lw = text_size(d, line, lf)[0]
        d.text(((width - lw) / 2, ty), line, font=lf, fill=0, anchor="la")
        ty += lh + (lead if i < len(set_lines) - 1 else 0)

    y = height - inset - PAD - bottom + 10
    y = rule(d, x0, x1, y, weights=(4, 2), gap=4, style=RULE_STYLE,
             middle=lion_w + 40 if lion_w else inner_w * 0.2)

    # the holders, one column each side of the lion
    row_y = y + 8
    if lion_img:
        img.paste(lion_img.convert("L"),
                  (int((width - lion_img.width) / 2),
                   int(row_y + (holder_h - lion_img.height) / 2)))
    block_top = row_y + (holder_h - (small_h * 2 + 4)) / 2
    for col, centre in zip(columns, (x0 + column / 2, x1 - column / 2)):
        if not col:
            continue
        for i, part in enumerate(col):
            pw = text_size(d, part, small)[0]
            d.text((centre - pw / 2, block_top + i * (small_h + 4)), part,
                   font=small, fill=0, anchor="la")
    y = row_y + holder_h + 10
    y = rule(d, x0, x1, y, weights=(2,), gap=0)

    if fine:
        fw = text_size(d, fine, fine_f)[0]
        d.text(((width - fw) / 2, y + 8), fine, font=fine_f, fill=0)
    if serial:
        d.text((x0, y + 6), serial, font=fine_f, fill=0)

    # the colophon sits below the frame, in the white the cutter leaves anyway
    credit_img = stamp(credit, int(width * CREDIT_W)) if credit else None
    extra = (credit_img.height + CREDIT_PAD + CREDIT_TAIL) if credit_img else 0

    body = Image.new("L", (width, height + extra), 255)
    body.paste(img, (0, 0))
    if credit_img:
        body.paste(credit_img.convert("L"),
                   (int((width - credit_img.width) / 2), height + CREDIT_PAD))
    if UPSIDE_DOWN if flip is None else flip:
        body = body.rotate(180)          # the ticket turns; the margin does not

    sheet = Image.new("L", (sheet_w, body.height), 255)
    sheet.paste(body, (margin, 0))
    return sheet.point(lambda v: 0 if v < 128 else 255, mode="1")


BAND = 64                  # rows per raster command


def raster_bytes(img, cut=True, feed=3, band=BAND):
    """ESC/POS raster: GS v 0, one bit per dot, 1 = black, rows top to bottom.

    Sent in horizontal bands rather than one command. A whole 576x576 image is
    41 kB, which overruns the print buffer on these machines: the printer takes
    the start, drops the middle and resumes, leaving a blank strip in the same
    place every time."""
    img = img.convert("1")
    w, h = img.size
    row_bytes = (w + 7) // 8
    pixels = img.load()
    # Darkness and speed are NOT set here. The ESC/POS density and speed commands
    # (GS ( K) are not implemented on this printer, which prints their bytes as
    # characters instead of obeying them. Those settings live in the printer's own
    # setup utility - the self-test's "Dark Scale" and "Print Speed" values.
    data = bytearray(b"\x1b@")                       # initialise
    for top in range(0, h, band):
        rows = min(band, h - top)
        packed = bytearray(row_bytes * rows)
        for y in range(rows):
            base = y * row_bytes
            for x in range(w):
                if pixels[x, top + y] == 0:          # black
                    packed[base + (x >> 3)] |= 0x80 >> (x & 7)
        data += b"\x1dv0\x00"
        data += bytes([row_bytes & 0xff, row_bytes >> 8, rows & 0xff, rows >> 8])
        data += packed
    data += b"\n" * feed
    if cut:
        data += b"\x1dVB\x00"
    return bytes(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fortune",
                    default="Make no plans around the quiet after the phone rings "
                            "until the frost.")
    ap.add_argument("--bearer", default="BEARER: BEN, 43")
    ap.add_argument("--prior", default="PRIOR HOLDER: INES, 52")
    ap.add_argument("--serial")
    ap.add_argument("--out", default="ticket.png")
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--credit-width", type=float, default=None,
                    help="credit width as a fraction of the ticket, e.g. 0.8")
    ap.add_argument("--margin", type=int, default=None,
                    help="dots to shift right for paper walk; added to the width "
                         "while it fits the head's 640 dots")
    ap.add_argument("--ratio", type=float, default=RATIO,
                    help="height as a multiple of width: 1.0 square, 1.25 taller. "
                         "Past about 1.6 it reads as a receipt again.")
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--printer", default="file:ticket.out")
    ap.add_argument("--demo", action="store_true", help="a long and a short fortune")
    ap.add_argument("--band", type=int, default=BAND,
                    help="rows per raster command; lower it if bands go missing")
    ap.add_argument("--flip", action="store_true",
                    help="print rotated 180 degrees")
    ap.add_argument("--feed", type=int, default=3,
                    help="blank lines after the ticket; the cutter adds its own")

    a = ap.parse_args()

    for label, path in (("title", TITLE_FONT), ("fortune", BODY_FONT),
                        ("clerical", CLERK_FONT)):
        print(f"  {label:9s} {path or '(not found, using a fallback)'}")
    for label, path in (("lion", LION), ("credit", CREDIT)):
        if not os.path.isfile(path):
            print(f"  {label:9s} {path} not found, leaving it out")

    if a.credit_width:
        globals()["CREDIT_W"] = a.credit_width

    jobs = [a.fortune]
    if a.demo:
        jobs = [a.fortune, "A door left unlocked will outlast the winter."]
    for i, text in enumerate(jobs):
        img = draw_ticket(text, a.bearer, a.prior, serial=a.serial,
                          width=a.width, ratio=a.ratio, margin=a.margin,
                          flip=True if a.flip else None)
        out = a.out if len(jobs) == 1 else a.out.replace(".png", f"_{i + 1}.png")
        img.save(out)
        print(f"wrote {out}  ({img.width}x{img.height} dots, "
              f"{img.width / 8:.0f}x{img.height / 8:.0f} mm at {DPI}dpi)")
        if a.do_print:
            import ticket
            print(ticket.send_raw(raster_bytes(img, band=a.band, feed=a.feed),
                                  a.printer))


if __name__ == "__main__":
    sys.exit(main() or 0)
