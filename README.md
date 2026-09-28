# Del Roar renderer

A local, fullscreen renderer for the Linux NUC. It draws the pre-rendered puppet
clips and a split-flap board on one screen, and takes orders from show logic over
a local socket. It knows nothing about fortunes, intake, or the handset.

```
show logic  ──JSON lines over a unix socket──▶  renderer.py  ──▶  the screen
(PHP, Python, anything)                              │
                                                     ├── clips.py   libmpv, clip queue, timeline cues
                                                     ├── board.py   split-flap state machine
                                                     └── control.py the socket
```

## Files

| File | What it is |
|---|---|
| `renderer.py` | The program you run. Window, OpenGL drawing, main loop, settings at the top. |
| `board.py` | Split-flap state machine, ported from `flapboard.html`. No graphics; testable on its own. |
| `clips.py` | Clip queue on one persistent libmpv instance, plus timeline cue firing. |
| `control.py` | The socket and the JSON protocol. |
| `showclient.py` | A keyboard test client, so you can drive the renderer by hand. |
| `show.py` | The show logic: states, which clip plays when, what the knob means. Separate process. |
| `stt.py` | Whisper speech-to-text, pushing settled transcripts to rows 0 and 1. Separate process. |
| `audio.py` | The audio bed: music and vamps played independently of the video, with fades and ducking. |
| `makeclips.py` | Builds labelled placeholder clips with a shared rest frame, for testing without real clips. |
| `maketimeline.py` | Turns a voice track into a `.board.json` timeline, using Whisper's segment timings. |
| `splitshow.py` | Cuts one long voice track into per-beat clips and audio beds, then renders them all. |

## Install

Everything here is free and open source.

```bash
sudo apt install libmpv2 mpv          # if libmpv2 isn't found, try libmpv1
python3 -m pip install pygame-ce moderngl mpv numpy
```

* **pygame-ce** creates the window and loads fonts (pygame.org). Plain `pygame` works too; the renderer handles the difference.
* **moderngl** is a Python wrapper around OpenGL, used to draw the board (github.com/moderngl/moderngl).
* **mpv** here is the `python-mpv` binding that talks to libmpv (github.com/jaseg/python-mpv).
* **libmpv** is the video player itself, as a library rather than an application.

## Run

```bash
python3 renderer.py --demo --windowed --scale 0.5   # board only, cycling strings, no clips
python3 renderer.py --no-video                      # board only, driven by showclient
python3 renderer.py --clips ./clips                 # the real thing, fullscreen
```

Escape or Q quits. In another terminal:

```bash
python3 showclient.py                     # interactive
python3 showclient.py board "TWO TRUTHS" "MUST BE SPOKEN"
python3 showclient.py play line04 idle
python3 showclient.py watch               # just print events
```

## Tonight's test, all four processes

```bash
python3 makeclips.py --dir clips          # dummy clips, once
python3 renderer.py --clips clips         # terminal 1
python3 show.py --trace                   # terminal 2
python3 stt.py --device N --model base.en # terminal 3
```

Lift the handset (or press and hold W), and the show runs: chapter clip, then
listening, then speak and the board shows your words, then turn the knob (A or
D) to choose and hold until the meter fills.

`renderer.py --knob-demo --no-video` exercises just the knob row with a sample
transcript, no clips or microphone needed.

## From one long track to clips and beds

```bash
# label the beats in Audacity (Ctrl-B per beat; " bed" suffix marks a vamp),
# File > Export > Export Labels
python3 splitshow.py labels labels.txt        # -> show_plan.json
python3 splitshow.py cut full.wav             # -> cuts/*.wav
python3 splitshow.py render del-roar.psd      # -> clips/*.mp4 and audio/*.wav
```

`splitshow.py detect full.wav` guesses the boundaries from silences instead of
labels, which works where the gaps are actually quiet. Where the vamps have music
under them, labelling by ear is the reliable route. Editing `show_plan.json`
by hand is expected either way: it holds a name, start, end, and a kind of
`clip`, `bed` or `skip` per segment.

`render` skips clips that already exist, so it can be interrupted and resumed,
and it renders a silent idle loop at the end.

## Rough cut from one long track

Before splitting the script into per-beat clips, render it as a single clip and
caption it automatically:

```bash
python3 puppet.py del-roar.psd --audio full.wav -o clips/full.mp4
python3 maketimeline.py full.wav --out clips/full.board.json
python3 renderer.py --clips clips --idle full
```

`maketimeline.py` runs Whisper over the track, wraps each line to the board, and
pages long lines across their delivery. Cues are pulled `--lead` seconds early
(1.2s by default) so the flapping finishes about when the voice arrives. The
output is a plain JSON file meant to be hand-edited afterwards; `--keep tail`
keeps only the end of a long line instead of paging, and `--segments file.json`
skips Whisper if you already have timings.

## Running by itself, and one command to restart it

Three systemd user services run the piece: the renderer, the show logic, and
speech. They start at boot, restart if they crash, and `delroar` drives all three.

```bash
bash ~/delroar/service/install.sh
sudo systemctl disable --now display-image.service   # the photo service holds the screen
delroar start
```

| Command | What it does |
|---|---|
| `delroar start` / `stop` | start or stop the whole piece |
| `delroar restart` | the single restart point: renderer first, then show logic and speech |
| `delroar status` | one line per service |
| `delroar logs [n]` | the last n lines from all three |
| `delroar follow` | live log, Ctrl-C to stop |
| `delroar enable` / `disable` | start at boot, or not |

Over SSH from anywhere on the tailnet, restarting the piece is one line:

```bash
ssh localuser@delroar-nuc delroar restart
```

Notes:
* **Lingering** is what lets the services run with nobody logged in; `install.sh`
  turns it on.
* **Show logic and speech are bound to the renderer**, so restarting the renderer
  restarts them too, and neither sits on a dead socket.
* **The clip folder, audio folder and idle clip** are set in
  `~/.config/systemd/user/delroar-renderer.service`; the microphone device is in
  `delroar-stt.service`. After editing either, run `systemctl --user daemon-reload`
  then `delroar restart`.
* **`display-image.service`** and the renderer both want the screen, so leave the
  photo service disabled while the piece is installed.

## Inputs

Makey Makey holds a key down while its contact is closed, so the renderer reads
states rather than keystrokes and reports changes:

```json
{"event":"input","name":"handset","state":"up"}
{"event":"input","name":"selector","state":"left"}
{"event":"input","name":"selector","state":"none"}
```

Set `INPUTS` at the top of `renderer.py` to match your wiring; it currently
expects W for the handset and A/D for the rotary switch. `DEBOUNCE` is how long
a contact must hold before it counts.

## The knob row

`{"cmd":"knob","left":"SAY IT AGAIN","right":"LET IT STAND"}` arms it. While the
knob is centred the row alternates `< SAY IT AGAIN` and `LET IT STAND >` every
`KNOB_DETENT_CYCLE` seconds. Turning it shows that label with a meter of solid
cells filling toward it, one cell every `KNOB_PER` seconds; at `KNOB_STEPS` the
renderer emits:

```json
{"event":"knob","side":"right","filled":4}
{"event":"activated","side":"right","label":"LET IT STAND"}
```

Turning away or centring the knob clears the meter in one flip per cell. The
row's cells get their own short drums while armed, compiled from every string
the row can show, so each change takes one or two flips instead of travelling
the full drum. `{"cmd":"knob","off":true}` disarms and restores the full drum.

Label pairs should be the same length, so the meter is identical in both
directions: `SAY IT AGAIN` and `LET IT STAND` are both 12, leaving 7 meter
cells at 20 columns.

## Protocol

Line-delimited JSON on `/tmp/delroar-renderer.sock`. On Windows, which has no
Unix sockets, it listens on `127.0.0.1:8765` instead; `--port N` sets that
explicitly on either platform. `showclient.py` tries the Unix socket first and
falls back to TCP, so the same commands work on both.

Commands in:

```json
{"cmd":"play",  "clip":"line04", "then":"idle"}
{"cmd":"play",  "clip":"line04", "now":true}
{"cmd":"loop",  "clip":"idle"}
{"cmd":"board", "lines":["I STAY UP AT NIGHT","WORRYING ABOUT THE"]}
{"cmd":"board", "rows":{"0":"THE LAST TRAIN HOME","1":{"text":"BEFORE THE STORM","align":"center"}}}
{"cmd":"knob",  "left":"SAY IT AGAIN", "right":"LET IT STAND"}
{"cmd":"knob",  "off":true}
{"cmd":"play",  "clip":"idle", "cues":false}
{"cmd":"utterance", "text":"the last train home"}
{"cmd":"board", "lines":["THE LAST TRAIN HOME"], "hold":4.0}
{"cmd":"board", "lines":["READY"], "snap":true}
{"cmd":"clear"}
{"cmd":"stop"}
{"cmd":"status"}
```

Events out:

```json
{"event":"started","clip":"line04"}
{"event":"ended","clip":"line04"}
{"event":"cue","clip":"line04","id":"stem_dread"}
{"event":"settled","board":["...","...","..."]}
{"event":"status","clip":"line04","pos":3.21,"board":["...","...","..."]}
{"event":"input","name":"handset","state":"up"}
{"event":"knob","side":"left","filled":3}
{"event":"activated","side":"left","label":"SAY IT AGAIN"}
{"event":"utterance","text":"the last train home"}
{"event":"error","message":"clip not found: clips/line99.mp4"}
```

A `board` command with `rows` touches only the rows named, so Whisper can own
rows 0 and 1 while the knob owns row 2 without either wiping the other. A live
string from speech recognition is just such a command. It uses the
same path as a timeline string, so the two can't collide inside the renderer.

## Audio beds, separate from the video

Video loops can be short, light and silent, cutting only on the shared rest
frame. The sound under them runs on its own clock in a second libmpv instance,
so a vamp can dissolve the moment the visitor answers while the video keeps
looping until it reaches a boundary.

```json
{"cmd":"audio","track":"vamp_1","fade_in":0.6,"loop":true}
{"cmd":"audio","duck":0.25,"over":0.3}
{"cmd":"audio","stop":true,"fade_out":1.4}
```

Tracks live in `audio/` (`AUDIO_DIR`, `AUDIO_EXT`). `BED_DEVICE` is its own
output, so the bed can go to a cabinet speaker while the clips' voice goes to
the handset. Clips that carry voice duck the bed automatically to `BED_DUCK`
and bring it back when they end; set `BED_DUCK = 1.0` to turn that off.
Events come back as `{"event":"audio","track":"vamp_1","state":"stopped"}`.

`makeclips.py --silent` writes video with no audio track, plus two dummy vamp
tones in `audio/`, which is the arrangement this is designed around.

## Clips and seamless switching

Put the rendered clips in `clips/` as `line04.mp4`, `idle.mp4`, and so on. One
persistent libmpv instance plays them with `keep-open`, so the last frame stays
on screen while the next file opens. Because every clip from `puppet.py` starts
and ends on the same rest frame, that change is invisible.

By default a queued clip waits for the current clip to finish, which is where the
rest frame is. `"now": true` cuts immediately, which will be visible if the
current clip is mid-motion. When nothing is queued, the `then` clip loops.

## Timeline strings

Next to `line04.mp4`, put `line04.board.json`:

```json
{"cues": [
  {"t": 0.0,  "id": "opening",    "lines": ["", "LIFT THE HANDSET"]},
  {"t": 6.4,  "id": "stem_dread", "lines": ["I STAY UP AT NIGHT", "WORRYING ABOUT THE"]},
  {"t": 14.0, "id": "clear",      "lines": ["", ""]}
]}
```

Cues fire against mpv's own playback position, so the board keeps step with the
voice even if a frame is dropped. Each cue also emits a `cue` event, so show
logic can hang its own behaviour off the same moment (opening the microphone,
say) without tracking time itself.

## Settings

At the top of `renderer.py`:

* `SCREEN_W/H` — 1080×1920 for the 43" panel in portrait.
* `INPUTS`, `DEBOUNCE`, `KNOB_ROW`, `KNOB_STEPS`, `KNOB_PER`, `KNOB_DETENT_CYCLE` — the hardware and the knob row.
* `COLS`, `ROWS`, `BOARD_WIDTH`, `BOARD_TOP`, `ALIGN` — currently 20×3 across 80% of the width, near the top.
* `PALETTE` — `carnival` (white cards, black ink; the default), plus `classic`, `amber`, `paper`, and `signal` from `flapboard.html`. Light palettes automatically use gentler flap shading, set by `SHADE_DEPTH`.
* `VIDEO_FIT` — `cover` fills the screen and the board draws over the top band; `below` shrinks the video to fit under the board.
* `AUDIO_DEVICE` — `auto`, or the handset, e.g. `alsa/plughw:CARD=Handset,DEV=0`. List devices with `mpv --audio-device=help`.
* `HWDEC` — `auto-safe`; set to `no` if video decoding misbehaves on this NUC.
* `FLAP_SOUND`, `FLAP_VOLUME`, `FLAP_DEVICE` — synthesised flap clicks. `FLAP_DEVICE` takes an SDL device name so the clicks can go somewhere other than the handset; `--mute` turns them off for a run.
* `FONT_CANDIDATES` — first match wins. `sudo apt install fonts-liberation` gives you Liberation Sans Narrow.

## Differences from flapboard.html

* The state machine, timing (62ms per flap), per-cell jitter, stagger, and easing are carried over exactly, and the cascade is deterministic from a fixed seed.
* The drum gained `<`, `>` and a solid block for the knob's meter, so it is 56 characters rather than 53. Cells can also carry their own short drum, which the original had no concept of.
* Drawing is faked in 2D rather than lit 3D: the falling flap foreshortens and darkens, and the half it lands on is shadowed. At around 40px per tile this reads much the same and costs far less to draw.
* The flap clicks are synthesised here rather than sampled: a noise snap plus a low knock, six variants, one per frame with volume scaled by how many cells landed. The HTML version's Web Audio clicks aren't reused.
* No camera controls, palette switching at runtime, or recording; the board is one fixed region of the screen.

## Known gaps

* At 20 columns across 80% of 1080px, tiles land near 40px wide, below the ~45px point where the hinge and shading start to look crude. Full width gives about 50px, and 16 columns gives about 62px.
* Startup shows a blank board. If you want text on the attract screen, send a `board` command with `"snap": true`.
* Nothing restarts the renderer if it exits. A systemd user service is the usual fix for an unattended month.
