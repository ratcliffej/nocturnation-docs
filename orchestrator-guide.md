# Music orchestrator guide

The NocturNation **music orchestrator** is a laptop-side daemon that
listens to whatever the operating system says is playing, looks up the
track in a per-song `.cues` file, and emits DMX universe state to the
NocturNation StickC for onward fanout to the Lume fleet. Each song
gets its own choreography; the orchestrator follows the music timeline
and fires FX cues at the right moments.

It is the alternative to driving a NocturNation show from QLC+ by hand
(see [`qlc-plus-beginners-guide.md`](qlc-plus-beginners-guide.md)). Both
producers speak the same DMX surface to the StickC, so you can hand
the show off between them mid-set if you want.

## When to use which

| You want to | Use |
|---|---|
| Programmed show synchronised to a specific playlist | Music orchestrator |
| Live operator control with faders, dials, cue stacks | QLC+ |
| Ambient wash for a venue, no audio | Either (both expose the same `quiet_wash` / `drift_wash` surface) |
| One co-running, one as backup | Both - the orchestrator falls back to Art-Net producer mode if it sees the QLC+ shim already holding the USB port |

## What it needs

| Layer | Requirement |
|---|---|
| Audio source | Any application that registers with the host's media-key API. macOS: Apple Music, Spotify, browsers via Media Session, etc. |
| Now-playing detection | macOS: `nowplaying-cli` (`brew install nowplaying-cli`). Windows: `pip install winsdk`. Linux: `pip install pydbus` plus the GObject runtime. |
| Hardware | One StickC (Plus2 or S3) running in **DMX Bridge** mode, USB-connected. Lume devices on the same ESP-NOW channel. |

## Quick start

From a terminal in the repo root::

```sh
# macOS
brew install nowplaying-cli
./Docs/tools/run-orchestrator-macos.sh

# Windows
Docs\tools\run-orchestrator-windows.bat
```

The wrapper creates a local Python venv on first run, installs the
dependencies, and exec's the orchestrator. Subsequent runs start
immediately.

Start a song in any audio app. The orchestrator's stdout shows the
match path::

```text
orchestrator: started (songs_dir=.../songs, default_bpm=120, output=usb, debug=off)
matcher: coldplay-fix-you -> coldplay-fix-you.cues
```

If no matching cue file exists, the matcher falls back to
`_default.cues`, which ships with a soft blue wash.

## File-naming convention

The matcher slugifies the track's artist and title into a single
hyphen-separated lower-case token and looks for a matching file in
`Docs/songs/`. The transformation is unicode-fold, ASCII-strip,
lower-case, runs of non-alphanumerics collapsed to `-`, leading and
trailing `-` removed::

| Now playing | Slug | File the matcher looks for |
|---|---|---|
| Coldplay - Fix You | `coldplay-fix-you` | `Docs/songs/coldplay-fix-you.cues` |
| Sigur Rós - Hoppípolla | `sigur-ros-hoppipolla` | `Docs/songs/sigur-ros-hoppipolla.cues` |
| AC/DC - T.N.T. | `ac-dc-t-n-t` | `Docs/songs/ac-dc-t-n-t.cues` |

The debug log line shows the slug form directly, so you can copy it
straight from the log to a new filename when programming a new song.

`_default.cues` is the fallback when the slug doesn't match anything.

## Authoring a `.cues` file

A cue file is one event per line, whitespace-separated, with `@`
directives at the top and `#` comments anywhere. The full spec is in
[`fx-library.md`](fx-library.md#cue-file-format); the short version::

```text
# Coldplay - Fix You
@artist     Coldplay
@title      Fix You
@bpm        138
@default_fx quiet_wash 20 40 80

# --- Intro ---
00:00     quiet_wash       20 40 80
# "When you try your best..."
00:13.40  sparkle_on_beat  80 200 200 100
00:35.5   sparkle_on_beat  255 0 255 100

# --- Build ---
01:20     linear_buildup   255 0 0 100 64  --buildup 8

# --- Drop ---
01:28     strobe_burst     5 255
01:30.250 sparkle_on_beat  255 255 255 100

# --- Outro ---
02:55     fade_to_black                       --buildup 4
03:00     stop
```

Time supports `MM:SS`, `M:SS`, and `H:MM:SS`, optionally with one to
three fractional-second digits (`MM:SS.x`, `MM:SS.xx`, `MM:SS.xxx`).
At 120 BPM each beat is 500 ms, so use centisecond grain
(`MM:SS.xx`) for beat-aligned cues - it also matches LRC lyric
timestamps exactly.

`stop` is the cancel sentinel: equivalent to "no FX". Use it to end
a song cleanly. For an explicit fade-out, schedule `fade_to_black`
before the `stop`.

## Available FX

The current library is generated from the FX classes themselves; the
authoritative listing lives in [`fx-library.md`](fx-library.md). At
the time of writing:

| ID | Cue name | Category | What it does |
|---|---|---|---|
| 1 | `quiet_wash` | ambient | Sustained single-colour wash. The default ambient bed. |
| 2 | `drift_wash` | ambient | Two-colour wash that cycles A ↔ B over the cycle time. |
| 11 | `sparkle_on_beat` | beat | Fires one pulse per beat at the supplied BPM. |
| 12 | `pulse_per_bar` | beat | Fires one pulse every N beats (default 4 = one per bar). |
| 13 | `group_cascade` | beat | Rotates a pulse around groups 1..N, one beat per group. |
| 21 | `linear_buildup` | buildup | Ramps Master and Pulse Probability over `buildup_s` seconds. |
| 32 | `strobe_burst` | drop | Max strobe rate for a short window, then auto-finish. |
| 41 | `fade_to_black` | transition | Ramps Master from start value to 0 over `buildup_s` seconds. |

Each entry in [`fx-library.md`](fx-library.md) documents its
parameters, ranges, and defaults.

## Lyric anchors

A comment line starting with a timestamp - the format
`gen_cues_skeleton.py` emits - is lifted out of the comment stream
and surfaced in `--debug` mode as the song crosses each anchor::

```text
# 00:13.40  When you try your best
00:13.50 sparkle_on_beat 80 200 200 100
# 00:20.20  But you don't succeed
```

Skeleton-generator placeholders (`# 00:30  TODO: cue here`) are
silently dropped so they don't pollute the lyric stream.

## Generating a starter file from lyrics

For songs published on [lrclib.net](https://lrclib.net) (free, no
auth), the orchestrator ships an authoring helper that pre-stamps a
`.cues` skeleton with timed lyric anchors::

```sh
Docs/tools/scripts/gen_cues_skeleton.py "Coldplay" "Fix You"
# -> writes Docs/songs/coldplay-fix-you.cues
```

You then open the file and replace the `# MM:SS  TODO: cue here`
markers with actual cue lines. The lyric anchors stay as comments
and surface in `--debug` mode so you can hear what you're cueing
to.

## Output modes

`--output {auto, usb, artnet}`. The default `auto` tries USB first
and falls back to Art-Net if the StickC's USB port is already busy
(typical when the QLC+ shim is running)::

| Mode | What it does | When to use |
|---|---|---|
| `usb` | Writes Enttec Pro frames straight to the StickC over USB | Standalone, no other software needed |
| `artnet` | Emits ArtDmx packets to `127.0.0.1:6454` | Run alongside QLC+ - your shim picks up either source |
| `auto` (default) | Try USB; fall back to Art-Net if the port is busy | What you usually want |

## Debug mode

`--debug` adds one log line per:

- now-playing poll (with the live position and play state)
- cue file load (cue count, lyric-anchor count, default FX, default BPM)
- cue admission (cue name, params, BPM and buildup overrides)
- lyric anchor crossed

Example::

```text
orchestrator: started (songs_dir=.../songs, default_bpm=120, output=usb, debug=on)
[00:14.000] poll:  coldplay-fix-you (playing=yes)
matcher: coldplay-fix-you -> coldplay-fix-you.cues
loaded: 8 cues, 27 lyric anchors, default_fx_id=1, default_bpm=138
[00:14.000] cue:   sparkle_on_beat  80 200 200 255
[00:14.000] lyric: When you try your best
```

The position shown is the orchestrator's interpolated estimate;
`(playing=yes/no)` reflects the host's playback state.

## Troubleshooting

### "Position stays at 00:00.000 even though a track is playing"

You are on an old `nowplaying-cli` build whose per-field `get`
subcommand returns 0 for `elapsedTime`. The orchestrator works
around this by calling `get-raw` and parsing JSON; if you still see
zero positions, run::

```sh
nowplaying-cli get-raw
```

and check that `kMRMediaRemoteNowPlayingInfoElapsedTime` advances
while a track plays. If even `get-raw` returns 0, the audio app
probably hasn't registered properly with MediaRemote (close and
reopen the app).

### "StickC LCD shows ACTIVE constantly"

By design - while any FX is loaded the orchestrator writes the
universe each tick, even if values are stable. The StickC's mapper
collapses identical inputs into a single LIGHT_WASH on the ESP-NOW
side, so the Lume sees no extra traffic. From v2 (post-2026-06-14)
the orchestrator suppresses byte-identical USB sends, so a static
wash now produces ACTIVE flashes the same way QLC+ does.

### "Lume stays dark on orchestrator startup"

Fixed in dispatch-gating commit. Older builds dispatched an
all-zero universe at startup which poisoned the StickC mapper's
wash seed. Pull the latest `feat/epic-10-shared-library` and
re-run.

### "Frames at unexpected rate on the Lume"

The Lume's frame counter should climb at ~1 Hz on a static wash
(just the heartbeat). If it climbs faster, the StickC mapper is
re-emitting LIGHT_WASH frames. Most likely a cue file with a `cycle`
parameter > 0 (drift wash) or a pulse-trigger FX.

## How it fits together

```text
    audio app
       |
       v
   nowplaying-cli / SMTC / MPRIS    (~1 Hz polled)
       |
       v
   matcher  --slugify-->  Docs/songs/<slug>.cues
       |
       v
   cue scheduler  --advance-->  FX engine
                                 |
                                 v
                            DMX universe (512 bytes)
                                 |
                                 v
            ___________________________________________
           |                                           |
           v                                           v
   USB Enttec Pro                              Art-Net UDP 6454
           |                                           |
           v                                           v
       StickC (DMX Bridge mode)                 artnet-to-enttec-pro shim
           |                                           |
           v                                           v
       mapper -> LIGHT_WASH / LIGHT_PULSE         StickC (DMX Bridge mode)
           |
           v
       ESP-NOW broadcast
           |
           v
       Lume fleet
```

The FX engine writes a 512-byte DMX universe; the StickC's
`DmxChannelMapper` translates changes on channels 1-20 (and groups
1-9 at offsets 41-360) into LIGHT_WASH and LIGHT_PULSE wire frames
for the Lume fleet. Lume firmware is unchanged from the QLC+ path -
the orchestrator is just a different producer pointing at the same
input surface.

## See also

- [`fx-library.md`](fx-library.md) - authoritative FX listing, parameter reference, full cue file format spec.
- [`qlc-plus-beginners-guide.md`](qlc-plus-beginners-guide.md) - the alternative path for live operator control.
- [`manuals/user-manual.md`](manuals/user-manual.md) - the broader NocturNation operator manual.
- `Docs/tools/README.md` - tool-level reference for the orchestrator and the shim.
