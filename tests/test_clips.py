"""The clip queue: what mpv is asked to play, and when."""
import os
import queue

import clips


class FakeMPV:
    """mpv's playlist semantics: replace, append, clear, remove, auto-advance."""

    def __init__(self, player):
        self.pl, self.pos, self.player = [], -1, player
        self.blocking = 0

    def command_async(self, cmd, *args, callback=None):
        if cmd == "loadfile":
            name = os.path.basename(args[0])[:-4]
            if args[1] == "replace":
                self.pl, self.pos = [name], 0
                self.player.events.put(("loaded", None))
            else:
                self.pl.append(name)
                if self.pos < 0:
                    self.pos = 0
                    self.player.events.put(("loaded", None))
        elif cmd == "playlist-clear":
            self.pl, self.pos = ([self.pl[self.pos]], 0) if self.pl else ([], -1)
        elif cmd == "playlist-remove":
            del self.pl[int(args[0])]
            self.pos = max(0, self.pos - 1)

    def command(self, *a, **k):                 # anything blocking would hang the renderer
        self.blocking += 1

    def finish(self):
        if self.pos + 1 < len(self.pl):
            self.pos += 1
            self.player.events.put(("loaded", None))
        else:
            self.player.events.put(("eof", None))


def make_player(tmp_path, names=("idle_a", "idle_b", "idle_c", "segment_1", "segment_2")):
    for n in names:
        (tmp_path / f"{n}.mp4").write_text("")
    p = clips.Player.__new__(clips.Player)
    p.clip_dir, p.ext = str(tmp_path), ".mp4"
    p._init_state()
    p.mpv = FakeMPV(p)
    return p


def test_the_next_clip_is_always_queued_ahead(tmp_path):
    p = make_player(tmp_path)
    p.loop(["idle_a", "idle_b", "idle_c"])
    p.update()
    assert p.current == "idle_a"
    assert p.mpv.pl[p.mpv.pos + 1:] == ["idle_b"], "nothing queued: the join would stall"


def test_idles_cycle_and_the_playlist_does_not_grow(tmp_path):
    p = make_player(tmp_path)
    p.loop(["idle_a", "idle_b", "idle_c"])
    p.update()
    seen = [p.current]
    for _ in range(7):
        p.mpv.finish()
        p.update()
        seen.append(p.current)
    assert seen[:4] == ["idle_a", "idle_b", "idle_c", "idle_a"]
    assert len(p.mpv.pl) <= 2


def test_playing_during_an_idle_loop_cuts_in_at_once(tmp_path):
    p = make_player(tmp_path)
    p.loop(["idle_a", "idle_b"])
    p.update()
    p.play("segment_1", then=["idle_a", "idle_b"])
    p.update()
    assert p.current == "segment_1"


def test_a_clip_ending_reports_it_and_returns_to_idle(tmp_path):
    p = make_player(tmp_path)
    p.play("segment_1", then=["idle_a"], now=True)
    p.update()
    p.mpv.finish()
    events, _ = p.update()
    kinds = [(e["event"], e.get("clip")) for e in events]
    assert ("ended", "segment_1") in kinds
    assert p.current == "idle_a"


def test_a_missing_clip_is_reported_not_crashed(tmp_path):
    p = make_player(tmp_path)
    p.play("nope", now=True)
    events, _ = p.update()
    assert any(e["event"] == "error" for e in events)


def test_nothing_blocking_is_ever_called(tmp_path):
    p = make_player(tmp_path)
    p.loop(["idle_a", "idle_b"])
    for _ in range(20):
        p.update()
        p.mpv.finish()
    assert p.mpv.blocking == 0, "a blocking mpv call from the render thread can deadlock"
