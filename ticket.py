#!/usr/bin/env python3
"""
ticket.py - compose and print the Bill of Exchange.

The visitor gives up one answer; they receive a stranger's, rendered as a destiny
of the same valence. This module holds the frames, the vault of what previous
visitors gave up, and the printing.

  python3 ticket.py --demo                     compose and show one, print nothing
  python3 ticket.py --demo --print             ... and print it
  python3 ticket.py --selftest                 run every frame against sample answers
  python3 ticket.py --vault                    list what the vault holds

Printing (set PRINTER below, or pass --printer):
  file:<path>     write the raw bytes, for testing            (default: ticket.out)
  dev:<path>      straight to a printer device, e.g. dev:/dev/usb/lp0
  usb:<vid>:<pid> straight over USB, finding the printer's own endpoint,
                  e.g. usb:0x154f:0x154f   (needs pyusb; no python-escpos)
  escpos:usb:<vendor>:<product>   via python-escpos, e.g. escpos:usb:0x0416:0x5011
  escpos:serial:<port>            e.g. escpos:serial:/dev/ttyUSB0
  none            compose only

`dev:` needs nothing installed; python-escpos (free, pip install python-escpos)
is only needed for the escpos: forms, which handle cutting and USB directly.
"""
import argparse
import json
import os
import random
import re
import sys
import textwrap
import time

WIDTH = 32                 # characters per line: 32 for 58mm paper, 42 for 80mm
MINOR_AGE = 18             # under this, never named as someone else's PRIOR HOLDER
WITHHOLD_IF_AGE_UNKNOWN = True   # age missing or not understood: treat as a minor
VAULT = "vault.jsonl"      # what previous visitors gave up
PRINTER = "file:ticket.out"

HEADER = ["BILL OF EXCHANGE", "THE FANTABULOUS DEL ROAR", "FORTUNE EXCHANGER"]
FINE_PRINT = ("ALL FORTUNES FINAL. FLEXIBILITY OF "
              "FUTURE NEITHER CONFIRMED NOR DENIED.")

# Frames take one answer and make a destiny of it. {x} is the answer, normalised
# to second person. Dark ones stay avoidable: they instruct rather than condemn.
FRAMES = {
    "dark": [
        "SPEAK OF {x} TO NOBODY BEFORE FRIDAY.",
        "LET {x} WAIT UNTIL SPRING.",
        "WHATEVER YOU DECIDE ABOUT {x}, DECIDE IT SLOWLY.",
        "DO NOT LET {x} CHOOSE THE YEAR FOR YOU.",
        "MAKE NO PLANS AROUND {x} UNTIL THE FROST.",
    ],
    "light": [
        "{x} WILL OUTLAST THE WINTER.",
        "{x} WILL TAKE ONE THING AND LEAVE ANOTHER.",
        "{x} WILL DECIDE SOMETHING FOR YOU BEFORE THE YEAR TURNS.",
        "LET {x} WAIT UNTIL SPRING; IT WILL KEEP.",
        "WHATEVER YOU DECIDE ABOUT {x}, DECIDE IT SLOWLY.",
    ],
}

# Enough in the vault that the first visitor of the day still receives something.
SEED = [
    {"text": "the last train home", "valence": "dark", "name": "MARGUERITE", "age": 61},
    {"text": "a door left unlocked", "valence": "dark", "name": "OSCAR", "age": 34},
    {"text": "the quiet after the phone rings", "valence": "dark", "name": "INES", "age": 52},
    {"text": "a letter that was never sent", "valence": "light", "name": "HOLLIS", "age": 29},
    {"text": "a box of old tapes", "valence": "light", "name": "DELPHINE", "age": 71},
    {"text": "the summer they all came back", "valence": "light", "name": "AUGUST", "age": 46},
]

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
NUMBER_WORDS.update({w: (i + 2) * 10 for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())})

FIRST_PERSON = [
    (r"\bmy\b", "your"), (r"\bmine\b", "yours"), (r"\bme\b", "you"),
    (r"\bi am\b", "you are"), (r"\bi'm\b", "you are"), (r"\bi\b", "you"),
    (r"\bwe\b", "you"), (r"\bour\b", "your"), (r"\bus\b", "you"),
]


NAME_LEAD = re.compile(
    r"^(?:my name is|my name's|the name is|i am|i'm|it is|it's|this is|they call me|just)\s+",
    re.I)


def clean_name(value, max_words=2):
    """A name out of what someone says: 'Ben.' -> BEN, "my name's Ben Connors" ->
    BEN CONNORS. Punctuation goes; a whole sentence is trimmed to its first words."""
    if value is None:
        return None
    s = " ".join(str(value).split())
    s = NAME_LEAD.sub("", s).strip()
    s = re.sub(r"[^\w' -]", "", s).strip(" -'")
    if not s:
        return None
    return " ".join(s.split()[:max_words]).upper()


def parse_age(value):
    """An age from speech: '43.', 'forty three', "I'm 15" -> 43, 43, 15. None if
    nothing usable is there."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).lower()
    digits = re.findall(r"\d{1,3}", s)
    if digits:
        n = int(digits[0])
        return n if 0 < n < 120 else None
    total, seen = 0, False
    for word in re.split(r"[\s-]+", s):
        if word in NUMBER_WORDS:
            total += NUMBER_WORDS[word]
            seen = True
        elif seen:
            break
    return total if seen and 0 < total < 120 else None


def is_minor(age):
    """True when the bearer must not be named: under age, or age unknown."""
    n = parse_age(age)
    if n is None:
        return WITHHOLD_IF_AGE_UNKNOWN
    return n < MINOR_AGE


def normalise(text):
    """A visitor's answer as Del Roar re-titles it: second person, no full stop."""
    s = " ".join(str(text).split()).strip().rstrip(".!?,;:")
    low = s.lower()
    for pattern, repl in FIRST_PERSON:
        low = re.sub(pattern, repl, low)
    return low


def compose(answer, valence, bearer, prior, rng=random):
    """The lines of one ticket."""
    frame = rng.choice(FRAMES["dark" if valence == "dark" else "light"])
    body = frame.format(x=normalise(answer)).upper()
    lines = list(HEADER) + ["", body, ""]
    if bearer:
        lines.append(f"BEARER: {bearer}")
    if prior:
        lines.append(f"PRIOR HOLDER: {prior}")
    lines += ["", FINE_PRINT]
    return lines


def render(lines, width=WIDTH):
    """Centre the header and fine print, wrap the body, as plain text."""
    out = []
    for i, line in enumerate(lines):
        if not line:
            out.append("")
            continue
        centred = i < len(HEADER) or line == FINE_PRINT
        for part in textwrap.wrap(line, width) or [""]:
            out.append(part.center(width).rstrip() if centred else part)
    return "\n".join(out)


# ---------------- the vault ----------------
def vault_load(path=VAULT):
    rows = list(SEED)
    if os.path.isfile(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return rows


def vault_add(text, valence, name, age, path=VAULT):
    """What this visitor gave up, for someone else to receive. A minor's name is
    not written down at all: they are only ever read back as a prior holder, where
    the name would be withheld anyway, so there is no reason to keep it."""
    n = parse_age(age)
    keep_name = None if is_minor(age) else clean_name(name)
    row = {"text": text, "valence": valence, "name": keep_name,
           "age": n, "t": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def vault_draw(valence, exclude_text=None, path=VAULT, rng=random):
    """Someone else's answer of the same valence."""
    pool = [r for r in vault_load(path)
            if r.get("valence") == valence and r.get("text") != exclude_text]
    return rng.choice(pool) if pool else None


def who(name, age, as_prior=False):
    """How a holder is written. Bearers get their own name and age - it is their
    own information. A PRIOR HOLDER appears on a stranger's ticket, so a minor is
    never named there; the age alone carries no identity."""
    n = parse_age(age)
    if as_prior and is_minor(age):
        return f"AGE {n}" if n is not None else None
    clean = clean_name(name)
    if not clean:
        return f"AGE {n}" if n is not None else None
    return f"{clean}, {n}" if n is not None else clean


def issue(answer, valence, bearer_name=None, bearer_age=None, path=VAULT, rng=random):
    """The whole exchange: take this visitor's answer in, give a stranger's out."""
    donor = vault_draw(valence, exclude_text=answer, path=path, rng=rng)
    if donor is None:
        donor = {"text": answer, "name": None, "age": None}
    vault_add(answer, valence, bearer_name, bearer_age, path=path)
    return compose(donor["text"], valence, who(bearer_name, bearer_age),
                   who(donor.get("name"), donor.get("age"), as_prior=True), rng=rng)


# ---------------- printing ----------------
ESC, GS = b"\x1b", b"\x1d"


def escpos_bytes(text, cut=True, feed=4):
    """Plain ESC/POS: reset, centre the header block is already done in text, so
    just print, feed and cut. Kept dependency-free on purpose."""
    data = ESC + b"@"                                   # initialise
    data += text.encode("cp437", "replace") + b"\n"
    data += b"\n" * feed
    if cut:
        data += GS + b"V" + bytes([66, 0])              # partial cut
    return data


def usb_probe(vendor, product):
    """What the printer offers: configurations, interfaces and endpoints. python-escpos
    assumes endpoint 0x01, which many printers do not use."""
    import usb.core
    dev = usb.core.find(idVendor=vendor, idProduct=product)
    if dev is None:
        return f"no device {vendor:#06x}:{product:#06x}"
    lines = [f"device {vendor:#06x}:{product:#06x}"]
    for cfg in dev:
        lines.append(f"  configuration {cfg.bConfigurationValue}")
        for intf in cfg:
            lines.append(f"    interface {intf.bInterfaceNumber} "
                         f"class {intf.bInterfaceClass}")
            for ep in intf:
                direction = "OUT" if usb.util.endpoint_direction(ep.bEndpointAddress) == \
                    usb.util.ENDPOINT_OUT else "IN"
                kind = {0: "control", 1: "iso", 2: "bulk", 3: "interrupt"}.get(
                    usb.util.endpoint_type(ep.bmAttributes), "?")
                lines.append(f"      endpoint {ep.bEndpointAddress:#04x}  {direction:3s}  {kind}")
    return "\n".join(lines)


def usb_send(data, vendor, product):
    """Write ESC/POS bytes to the printer's first bulk OUT endpoint."""
    import usb.core
    import usb.util
    dev = usb.core.find(idVendor=vendor, idProduct=product)
    if dev is None:
        raise OSError(f"printer {vendor:#06x}:{product:#06x} not found")
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)      # usblp may have claimed it
    except (NotImplementedError, usb.core.USBError):
        pass
    try:
        cfg = dev.get_active_configuration()    # already configured from a previous print
    except usb.core.USBError:
        dev.set_configuration()
        cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    out = usb.util.find_descriptor(
        intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress)
        == usb.util.ENDPOINT_OUT and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK)
    if out is None:
        raise OSError("no bulk OUT endpoint: this printer may not speak ESC/POS over USB")
    written = 0
    try:
        for i in range(0, len(data), 4096):   # some printers choke on one big write
            written += out.write(data[i:i + 4096], timeout=5000)
    finally:
        usb.util.dispose_resources(dev)       # let go, so the next print can open it
    return f"printed {written} bytes to endpoint {out.bEndpointAddress:#04x}"


def usb_status(vendor, product):
    """Ask the printer how it is, with the real-time status commands. These are
    answered even when the printer is offline, out of paper or has its cover open,
    which is exactly when nothing comes out and nothing is reported."""
    import usb.core
    import usb.util
    dev = usb.core.find(idVendor=vendor, idProduct=product)
    if dev is None:
        return f"no device {vendor:#06x}:{product:#06x}"
    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except (NotImplementedError, usb.core.USBError):
        pass
    try:
        cfg = dev.get_active_configuration()
    except usb.core.USBError:
        dev.set_configuration()
        cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]
    out = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(
        e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
    inp = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(
        e.bEndpointAddress) == usb.util.ENDPOINT_IN)
    if inp is None:
        return "this printer has no IN endpoint, so it cannot answer a status query"
    meanings = {
        1: [(0x04, "drawer/feed"), (0x08, "OFFLINE"), (0x20, "cover open"),
            (0x40, "feed button held")],
        2: [(0x04, "cover open"), (0x08, "paper fed by button"), (0x20, "error"),
            (0x40, "error (recoverable)")],
        3: [(0x04, "auto-cutter error"), (0x08, "unrecoverable error"),
            (0x20, "print head too hot")],
        4: [(0x0C, "paper LOW"), (0x60, "paper OUT")],
    }
    report = []
    for n, bits in meanings.items():
        try:
            out.write(bytes([0x10, 0x04, n]), timeout=2000)
            reply = bytes(inp.read(8, timeout=2000))
        except usb.core.USBError as e:
            report.append(f"  status {n}: no answer ({e.strerror or e})")
            continue
        if not reply:
            report.append(f"  status {n}: empty answer")
            continue
        byte = reply[0]
        flags = [name for mask, name in bits if byte & mask == mask]
        report.append(f"  status {n}: {byte:#04x}" + (f"  -> {', '.join(flags)}" if flags else "  -> ok"))
    usb.util.dispose_resources(dev)
    return "\n".join(report)


def send(text, target=PRINTER):
    kind, _, rest = target.partition(":")
    if kind == "none":
        return "not printed"
    if kind == "file":
        with open(rest or "ticket.out", "wb") as f:
            f.write(escpos_bytes(text))
        return f"wrote {rest or 'ticket.out'}"
    if kind == "dev":
        with open(rest, "wb") as f:
            f.write(escpos_bytes(text))
        return f"sent to {rest}"
    if kind == "usb":
        vendor, product = (int(x, 16) for x in rest.split(":"))
        return usb_send(escpos_bytes(text), vendor, product)
    if kind == "escpos":
        from escpos import printer as esc                # python-escpos
        how, _, args = rest.partition(":")
        if how == "usb":
            vendor, product = (int(x, 16) for x in args.split(":"))
            p = esc.Usb(vendor, product)
        elif how == "serial":
            p = esc.Serial(args or "/dev/ttyUSB0")
        elif how == "network":
            p = esc.Network(args)
        else:
            raise ValueError(f"unknown escpos transport: {how}")
        p.text(text + "\n\n\n")
        p.cut()
        return f"printed via python-escpos ({how})"
    raise ValueError(f"unknown printer target: {target}")


# ---------------- command line ----------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answer", help="what this visitor gave up")
    ap.add_argument("--valence", choices=["dark", "light"], default="dark")
    ap.add_argument("--name")
    ap.add_argument("--age")
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--printer", default=PRINTER)
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--vault-file", default=VAULT)
    ap.add_argument("--demo", action="store_true", help="one of each valence")
    ap.add_argument("--selftest", action="store_true", help="every frame, sample answers")
    ap.add_argument("--vault", action="store_true", help="list the vault")
    ap.add_argument("--probe", metavar="VID:PID",
                    help="list a USB printer's endpoints, e.g. --probe 0x154f:0x154f")
    ap.add_argument("--status", metavar="VID:PID",
                    help="ask a USB printer whether it is offline, out of paper, etc.")
    ap.add_argument("--feed", metavar="VID:PID",
                    help="feed a few lines and cut: the simplest possible test")
    a = ap.parse_args()

    if a.status:
        vendor, product = (int(x, 16) for x in a.status.split(":"))
        print(usb_status(vendor, product))
        return

    if a.feed:
        vendor, product = (int(x, 16) for x in a.feed.split(":"))
        print(usb_send(ESC + b"@" + b"DEL ROAR TEST\n" + b"\n" * 6 + GS + b"V" + bytes([66, 0]),
                       vendor, product))
        return

    if a.probe:
        vendor, product = (int(x, 16) for x in a.probe.split(":"))
        print(usb_probe(vendor, product))
        return

    if a.vault:
        for r in vault_load(a.vault_file):
            print(f"  {r.get('valence','?'):5s} {r.get('text','')!r:45s} "
                  f"{who(r.get('name'), r.get('age')) or '-'}")
        return

    if a.selftest:
        samples = {"dark": ["my last dollar", "the phone call I keep not making"],
                   "light": ["a box of old tapes", "I want to see the northern lights"]}
        for valence, answers in samples.items():
            for ans in answers:
                print(f"\n--- {valence}: {ans!r} -> {normalise(ans)!r}")
                for frame in FRAMES[valence]:
                    print("   " + frame.format(x=normalise(ans)).upper())
        return

    if a.demo:
        rng = random.Random(3)
        for valence, ans in (("dark", "the last train home"),
                             ("light", "a letter that was never sent")):
            lines = issue(ans, valence, "BEN", 43, path=a.vault_file, rng=rng)
            print("\n" + "=" * a.width)
            print(render(lines, a.width))
            print("=" * a.width)
            if a.do_print:
                print(send(render(lines, a.width), a.printer))
        return

    if not a.answer:
        ap.error("give --answer, or use --demo / --selftest / --vault")
    lines = issue(a.answer, a.valence, a.name, a.age, path=a.vault_file)
    text = render(lines, a.width)
    print(text)
    if a.do_print:
        print(send(text, a.printer), file=sys.stderr)


if __name__ == "__main__":
    main()
