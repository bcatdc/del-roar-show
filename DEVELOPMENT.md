# Working on Del Roar

Three machines, one repo:

| Machine | Does | Needs |
|---|---|---|
| **Tower** (Windows) | renders clips from the PSD | `requirements-render.txt`, ffmpeg, the PSD |
| **Laptop** (Windows) | editing code, quick checks | `requirements-dev.txt`, optionally `requirements.txt` |
| **NUC** (Debian) | runs the piece | `requirements.txt`, libmpv, ffmpeg |

Code is in git. Clips, audio, the PSD and each machine's settings are not: they're
too big, they change constantly, or they differ per machine.

## First time

**Make the repo** (github.com → New repository → private → don't add a README),
then from the folder that has the code:

```bash
git init
git add .
git commit -m "Del Roar: renderer, show logic, board, ticket"
git branch -M main
git remote add origin https://github.com/<you>/del-roar.git
git push -u origin main
```

**On each other machine:**

```bash
git clone https://github.com/<you>/del-roar.git delroar
cd delroar
cp local.json.example local.json     # then edit for this machine
```

On the NUC, `local.json` is where `ROTATE`, the audio devices, the volume and the
clip folder live. Nothing in git overwrites them, so `delroar update` is safe.

## Day to day

```bash
git switch -c board-timing        # a branch per change
# ... edit ...
python -m pytest -q               # 22 tests, about 5 seconds, no hardware needed
python -m pyflakes *.py           # undefined names: what broke the renderer twice
git commit -am "board: halve the cascade stagger"
git push -u origin board-timing
```

Open a pull request on GitHub. The checks in `.github/workflows/ci.yml` run the
same two commands on every push. Merge to `main` when they pass.

## Deploying to the NUC

```bash
ssh localuser@delroar-nuc delroar update
```

That pulls `main`, prints what changed, and restarts the three services. If the
pull is refused, this machine has edits of its own: `git status` in `~/delroar`
shows them, and machine settings belong in `local.json`.

`delroar version` says what a machine is running, which is the first thing to
check when its behaviour doesn't match yours.

## Assets

Clips and audio stay out of git. From the tower:

```powershell
scp clips3\*.mp4 localuser@delroar-nuc:~/delroar/clips3/
scp audio2\*.wav localuser@delroar-nuc:~/delroar/audio/
```

Rendering stays on the tower; the NUC only plays.

## Tests

`tests/` covers the parts that need no GPU, sound card, printer or microphone:

* **`test_board.py`** — what the board shows, how long a change takes, that rows
  don't overwrite each other, that short drums keep the knob quick.
* **`test_clips.py`** — the clip queue against a stand-in for mpv: the next clip
  is always queued, idles cycle, the playlist never grows, and no blocking call is
  ever made from the render thread.
* **`test_control.py`** — commands in and events out, and that a client which
  never reads can't stall the renderer. That one froze the show twice.
* **`test_show.py`** — a whole visit: attract, wake, a listen with the mic opening
  and closing, and speech during a clip never reaching the board.
* **`test_ticket.py`** — composition, the vault, and that a minor is named on
  their own ticket but never as someone else's prior holder.

What tests can't reach: the GL rendering, libmpv, audio devices, the Makey Makey
and the printer. Those still need the NUC.

## Things worth knowing

* **`local.json` is per machine and never committed.** `local.json.example` shows
  what goes in it.
* **`vault.jsonl` isn't committed either.** It's visitor data, and each machine's
  copy is its own.
* **Undefined-name checks matter here.** Two of the crashes on the NUC were names
  that no longer existed, which `pyflakes` catches in a second.
