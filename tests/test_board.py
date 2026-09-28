"""The split-flap board: what it shows, how long it takes, and its short drums."""
from board import Board, BLOCK, CHARSET


def settle(board, limit=10.0):
    t = 0.0
    while not board.settled() and t < limit:
        board.update(1 / 60)
        t += 1 / 60
    return t


def test_text_lands_centred_and_settles_quickly():
    b = Board(20, 3)
    b.snap_to(["", "", ""])
    b.set_text(["THE LAST TRAIN HOME", "BEFORE THE STORM", ""])
    t = settle(b)
    assert b.read_text()[0].strip() == "THE LAST TRAIN HOME"
    assert t < 1.5, f"a full change took {t:.2f}s"


def test_rows_are_independent():
    b = Board(20, 3)
    b.snap_to(["ROW ZERO", "ROW ONE", ""])
    before = b.read_text()[0]
    b.set_row(2, "ROW TWO", "left")
    settle(b)
    assert b.read_text()[0] == before
    assert b.read_text()[2].strip() == "ROW TWO"


def test_short_drums_make_a_row_respond_in_a_flip_or_two():
    b = Board(20, 3)
    b.snap_to(["", "", ""])
    options = [("< SAY IT AGAIN", "left"), ("LET IT STAND >", "right")]
    options += [(f"SAY IT AGAIN {BLOCK * n}", "left") for n in range(8)]
    b.set_row_drums(2, options)
    assert max(len(b.cell(2, c).drum) for c in range(20)) <= 8
    b.set_row(2, "< SAY IT AGAIN", "left")
    assert settle(b) < 0.5


def test_unknown_characters_become_spaces():
    b = Board(20, 3)
    assert "Ü" not in b.sanitize("ÜBER")
    assert all(ch in CHARSET for ch in b.sanitize("Ümlaut & 42%"))


def test_inputs_can_be_wired_either_way_round(monkeypatch):
    """A handset whose contact is CLOSED in the cradle is the states swapped."""
    import pygame
    pygame.init()
    import renderer

    inputs = renderer.Inputs({"space": ("handset", "down", "up"),
                              "left": ("selector", "left")})
    held = {}
    monkeypatch.setattr(pygame.key, "get_pressed", lambda: _Pressed(held))
    codes = {pygame.key.name(c): c for c in inputs.mapping}

    def run(frames=8):
        out = []
        for _ in range(frames):
            out += inputs.update(1 / 60)
        return [(e["name"], e["state"]) for e in out]

    held[codes["space"]] = True
    assert ("handset", "down") in run(), "resting handset should read as down"
    held[codes["space"]] = False
    assert ("handset", "up") in run(), "lifting opens the contact"
    held[codes["left"]] = True
    assert ("selector", "left") in run()


class _Pressed(dict):
    def __init__(self, held):
        super().__init__()
        self.held = held

    def __getitem__(self, key):
        return self.held.get(key, False)
