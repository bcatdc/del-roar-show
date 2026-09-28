"""Hardware contacts read as states, including a handset that closes at rest."""
import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
pygame = pytest.importorskip("pygame")


@pytest.fixture
def inputs_module():
    pygame.init()
    import renderer
    return renderer


def drive(inp, pressed_codes, frames=8):
    class Fake(dict):
        def __getitem__(self, k):
            return k in pressed_codes
    pygame.key.get_pressed = lambda: Fake()
    events = []
    for _ in range(frames):
        events += inp.update(1 / 60)
    return [(e["name"], e["state"]) for e in events]


def test_a_cradle_contact_reads_the_right_way_round(inputs_module):
    inp = inputs_module.Inputs({"space": ("handset", "down", "up")})
    code = list(inp.mapping)[0]
    assert ("handset", "down") in drive(inp, {code}), "resting handset should read down"
    assert ("handset", "up") in drive(inp, set()), "lifted handset should read up"


def test_both_pygame_key_spellings_are_found(inputs_module):
    inp = inputs_module.Inputs({"space": ("handset", "up"), "w": ("handset", "up"),
                                "left": ("selector", "left")})
    assert len(inp.mapping) == 3, "named keys and letters are spelled differently in pygame"


def test_two_keys_for_one_input_do_not_fight(inputs_module):
    inp = inputs_module.Inputs({"left": ("selector", "left"), "right": ("selector", "right")})
    codes = {pygame.key.name(c): c for c in inp.mapping}
    assert ("selector", "right") in drive(inp, {codes["right"]})
    assert ("selector", "none") in drive(inp, set())
