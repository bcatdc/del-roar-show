"""The Bill of Exchange: composition, the vault, and who may be named."""
import random

import ticket


def test_answers_are_turned_around_to_second_person():
    assert ticket.normalise("my last dollar") == "your last dollar"
    assert ticket.normalise("I'm afraid of losing my job") == "you are afraid of losing your job"
    assert ticket.normalise("The last train home.") == "the last train home"


def test_names_survive_speech_recognition():
    for given, expect in [("Ben.", "BEN"), ("my name's Ben Connors", "BEN CONNORS"),
                          ("I'm Maya!", "MAYA"), ("", None), (None, None)]:
        assert ticket.clean_name(given) == expect, given


def test_age_survives_speech_recognition():
    for given, expect in [("43.", 43), ("forty three", 43), ("I'm 15", 15),
                          ("seventeen", 17), (None, None), ("banana", None)]:
        assert ticket.parse_age(given) == expect, given


def test_ticket_has_every_part(tmp_path):
    lines = ticket.issue("the last train home", "dark", "BEN", "43",
                         path=str(tmp_path / "v.jsonl"), rng=random.Random(1))
    text = ticket.render(lines)
    assert "BILL OF EXCHANGE" in text
    assert "BEARER: BEN, 43" in text
    assert "PRIOR HOLDER:" in text
    assert "ALL FORTUNES FINAL" in text


def test_a_minor_is_named_on_their_own_ticket_only(tmp_path):
    vault = str(tmp_path / "v.jsonl")
    own = ticket.render(ticket.issue("a door left unlocked", "dark", "MAYA", "15",
                                     path=vault, rng=random.Random(2)))
    assert "MAYA, 15" in own                       # their own name is theirs to have

    stored = [r for r in ticket.vault_load(vault) if r.get("age") == 15]
    assert stored and stored[0]["name"] is None    # not kept for anyone else

    as_prior = ticket.who("MAYA", "15", as_prior=True)
    assert as_prior == "AGE 15" and "MAYA" not in as_prior


def test_unknown_age_is_never_named_as_a_prior_holder():
    assert "SAM" not in (ticket.who("SAM", None, as_prior=True) or "")


def test_the_fortune_keeps_the_valence_it_was_given(tmp_path):
    vault = str(tmp_path / "v.jsonl")
    for valence in ("dark", "light"):
        body = ticket.issue("a box of old tapes", valence, "BEN", "43",
                            path=vault, rng=random.Random(4))[4]
        assert any(body.startswith(f.split("{")[0].upper()) or "{x}" in f
                   for f in ticket.FRAMES[valence])


def test_printer_bytes_open_and_cut(tmp_path):
    out = str(tmp_path / "t.out")
    ticket.send("HELLO", f"file:{out}")
    data = open(out, "rb").read()
    assert data.startswith(b"\x1b@") and data.endswith(b"\x1dVB\x00")


def test_only_approved_answers_reach_a_stranger(tmp_path):
    vault = str(tmp_path / "v.jsonl")
    ticket.vault_add("something nobody has read", "dark", "ZED", 40, path=vault)
    for seed in range(40):
        drawn = ticket.vault_draw("dark", path=vault, rng=random.Random(seed))
        assert drawn["text"] != "something nobody has read"
        assert drawn["moderated"] is True


def test_review_approves_and_rejects(tmp_path):
    vault = str(tmp_path / "v.jsonl")
    ticket.vault_add("the long drive north", "dark", "ZED", 40, path=vault)
    ticket.vault_add("something unkind", "dark", "YAN", 30, path=vault)
    answers = iter(["a", "r"])
    ticket.review(vault, ask=lambda _p: next(answers))
    rows = {r["text"]: r["moderated"] for r in ticket._read_rows(vault)[0]}
    assert rows == {"the long drive north": True, "something unkind": "rejected"}
    pool = {ticket.vault_draw("dark", path=vault, rng=random.Random(s))["text"]
            for s in range(60)}
    assert "the long drive north" in pool and "something unkind" not in pool
