# Cue authoring guide

> How to author a `.cues` file for a song — from blank page to a polished show. Covers the Epic 14 lyric-first + librosa-MIR pipeline, the `@offset` bench-tuning procedure, and the gotchas worth knowing about up front.

**Last updated**: 2026-06-27 (Epic 14 B5)
**Companion docs**:
- [`cue-file-schema.md`](cue-file-schema.md) — the canonical directive + cue-line reference.
- [`user-manual.md`](user-manual.md) — operator + LD guide for running NocturNation at a venue. Section 5.8 covers the orchestrator side.
- [`tools/README.md`](../tools/README.md) — per-tool reference for the authoring scripts.

---

## Overview

Authoring a cue file is a four-step process. Each step is optional and idempotent — re-running any step preserves prior hand-edits.

| Step | Tool | Time per song | What it does |
|---|---|---|---|
| **1. Lyric scaffold** | `cues_from_lyrics.py` | ~10 s | Fetches synced lyrics from [lrclib.net](https://lrclib.net), emits a starter `.cues` file with each line as a `BodyText:` cue. **No audio file required.** Always do this first if the song has known lyrics. |
| **2. MIR enrichment** | `audio_enrich_cues.py` | ~15 s + audio decode | Runs librosa on the audio file, refreshes the cue file's header with tempo / key / mode / duration / sections. Optionally snaps timestamps to beats (`--snap`) and seeds a first-pass FX scaffold (`--seed`). **Audio file required.** |
| **3. Hand-edit** | Your text editor | ~30 min | Rename `section1` / `section2` / ... to `verse1` / `chorus1` / `bridge` / etc. while listening through. Tweak the seeded FX choices. Add cues that aren't in the seed. |
| **4. Bench-tune `@offset`** | The orchestrator + your ears | ~5 min | Run the show against the actual music player, observe the timing offset between cue fires and the music, set `@offset` to cancel it. See [the procedure below](#step-4-bench-tune-offset). |

After step 4 the cue file is show-ready.

---

## Prerequisites

```bash
# One-time setup on the authoring machine (M3 Max recommended; works
# on any modern Mac / Linux / Windows-with-WSL).
cd Docs/tools
.venv/bin/pip install -r requirements.txt    # librosa + Pillow + others
brew install ffmpeg                          # needed for MP3 / M4A decode

# Verify librosa works (one-shot import test):
.venv/bin/python -c "import librosa; print(librosa.__version__)"
```

You also need:

- **An audio file** of the song. FLAC is best (lossless = sharper MIR); WAV equivalent; MP3 at 320 kbps is fine; MP3 at 128 kbps starts to degrade beat detection. Apple Music subscription downloads are FairPlay-DRM-encrypted and **not usable**; iTunes Store *purchases* are DRM-free AAC and work fine. Other usable sources: Bandcamp purchases, owned CD rips, Beatport (electronic), Free Music Archive (CC-licensed). Audio files are authoring-time only; never shipped at the show.
- **Internet** during step 1 (lrclib.net lyrics fetch). Step 2 + 3 + 4 are offline.

---

## Step 1: Lyric scaffold

```bash
.venv/bin/python scripts/cues_from_lyrics.py "Coldplay" "Higher Power"
# -> writes Docs/songs/coldplay-higher-power.cues
```

The artist + title pair feeds lrclib.net's API. The tool emits one `BodyText:` cue per LRC timestamp, plus a placeholder `@bpm 120` and a placeholder `@default_fx quiet_wash 20 40 80`.

**If lrclib has no synced lyrics for the track**, the tool emits a stub with just the header — you'll author the body cues from scratch. Comment-only tracks (instrumentals, mixes) take this path too.

**Non-Latin scripts** (Hangul / kana / kanji / Cyrillic / Greek / Arabic / etc.) trigger a warning to stderr and a `# WARNING:` block in the cue file's comment header. The Tildagon's bundled font (Arimo) is Latin-only; non-Latin glyphs render as missing-glyph boxes on the badge LCD. Romanise or translate the affected `BodyText:` lines before show — there's a worked example in [coldplay-x-bts-my-universe.cues](../songs/coldplay-x-bts-my-universe.cues) for the BTS verses.

The `--comment-anchors` flag retains the pre-Epic-13 output: lyrics as `#` comment anchors instead of real `BodyText:` cues. Use only if you want to hand-author the BodyText cues yourself.

---

## Step 2: MIR enrichment

```bash
.venv/bin/python scripts/audio_enrich_cues.py \
    ../songs/coldplay-higher-power.cues \
    --audio /path/to/coldplay-higher-power.flac
```

This rewrites the cue file's header with detected `@bpm`, `@time_sig`, `@key`, `@mode`, `@duration`, and a `@section section1..N` block (one per acoustic section). Hand-edited body cues are preserved verbatim; the `@analysis_synced` timestamp records when MIR last ran.

A sidecar `<cuefile>.analysis.json` (gitignored) holds the full librosa dump — beats, onsets, chroma, full sections array. Used by `--snap` and `--seed` below; the orchestrator never reads it at runtime.

### Useful flags

**`--snap`** quantises every cue-line timestamp to the nearest detected beat within ±150 ms. Useful when lrclib's lyric timestamps are 100-200 ms loose against the beat grid, or when hand-typed cues want crisp alignment.

```bash
.venv/bin/python scripts/audio_enrich_cues.py ../songs/x.cues --audio x.flac --snap
```

Cues outside the threshold (structural cues at unusual moments — the start of a wash, BodyText that lands mid-phrase by design) are left at their authored time. Override the threshold with `--snap-threshold-ms <N>` if needed.

**`--seed`** emits a first-pass FX scaffold based on section + key + mode + loudness:

- One `quiet_wash R G B` at each section start, colour derived from the track's key + mode (major = warm; minor = cool).
- One `sparkle_on_beat R G B prob group` at the next beat after, for sections with above-median loudness.

```bash
.venv/bin/python scripts/audio_enrich_cues.py ../songs/x.cues --audio x.flac --snap --seed
```

Seeded cues are tagged `# seed`. To bulk-delete after a hand-edit pass: `grep -v '# seed' file.cues > new.cues && mv new.cues file.cues`.

### Tempo gotcha — half / double picks

librosa picks the "dominant" tempo from the audio. For high-energy dance-pop with strong snare/hat layers, the dominant tempo is often the FAST value (e.g. Coldplay's *Higher Power* analyses as 178 BPM, but the song also has a valid half-time interpretation at 89 BPM). If the seeded sparkle cues feel manic at the bench, the half-time tempo is probably what you want — edit `@bpm` by hand for now (a `--tempo-hint <N>` flag is on the post-EMF roadmap).

---

## Step 3: Hand-edit

This is where you actually compose the show. The tool gives you a structured starting point; you make creative choices.

Typical hand-edit pass for a 3-minute pop song (~30 min):

1. **Rename sections**. Open the cue file in your editor. Listen to the track. As each section change happens, rename `section1` → `intro`, `section2` → `verse1`, `section3` → `chorus1`, etc. The librosa segmenter is usually accurate to within ±1-2 s of the actual song-structure boundary.
2. **Tweak seeded colours**. If the auto-palette doesn't fit the vibe (it's deterministic from the key, not from the song's actual aesthetic), pick your own RGB triples. Most cue files use 3-5 main colours that the LD identifies with the track.
3. **Add cues the seed missed**. The seeder is conservative — one wash per section, optional sparkle. Real shows want a richer texture: pulses on the kick, build-ups before drops, fades during quiet passages. Cue lines you add aren't tagged `# seed`, so a future `--seed` re-run won't try to deduplicate them.
4. **Remove cues that don't work**. The whole `# seed` block can be bulk-deleted with grep (see above) if the seed isn't useful for this track.

The `# --- chorus 1 ---` divider comments are author-added and survive re-runs of the tool. Use them liberally to make the cue file scannable.

---

## Step 4: Bench-tune `@offset`

The cue file's timestamps are sample-accurate against the audio file as analysed. **But** the runtime gap between "music player reports playhead at T" and "the cue at T actually fires on the wire" has a constant offset:

- The OS now-playing API (`nowplaying-cli` on macOS, MPRIS on Linux) has a polling cadence + reporting latency, typically 50-100 ms behind the actual player.
- The music player's audio buffering adds another 10-50 ms (the audio is rendered N ms ahead of what the OS reports as the "current" position).
- The orchestrator's poll loop runs at ~10 Hz; a fresh playhead reading is up to 100 ms old by the time it fires the next cue.

Total: typically **50-200 ms of constant delay** between music + visuals. The `@offset` directive cancels it. Positive offsets DELAY the cue file (visuals fire LATER than authored); negative offsets ADVANCE them.

### Bench procedure

Iterate on this ~3-5 times per track until the visuals lock to the music.

1. **Run the orchestrator with `--debug`**, in a terminal where you can read it while the music plays:

   ```bash
   ./run-orchestrator-macos.sh --debug
   ```

   You'll see lines like:

   ```
   [00:00.000] poll:  coldplay-viva-la-vida (playing=yes)
   [00:00.250] cue:   00:00.00  quiet_wash 200 60 130   # seed
   [00:11.500] cue:   00:11.50  BodyText: I'm so happy that I'm alive
   ```

   The `[MM:SS.fff]` is the orchestrator's clock (= reported player playhead). The second timestamp on each `cue:` line is the **authored** time. They typically differ by the offset we're trying to measure.

2. **Cue up a song with sharp, identifiable beats** (kick drum on 1 and 3 is easier than swing). Press play.

3. **Pick a specific authored cue you can hear** — a beat, a lyric onset, an FX trigger. Look at the orchestrator log when it fires. Compare to what you *hear* at that moment.

   - If the **visual fires BEFORE the audio** you're cueing against → add positive offset.
   - If the **visual fires AFTER the audio** → add negative offset.

   A useful first-pass calibration: pick the first lyric onset. Note its authored time (say `00:11.50`) and listen for that lyric in the music. If the visual flashes ~120 ms before you hear the lyric, set `@offset 0.12`.

4. **Edit the cue file**:

   ```
   @offset     0.12        # 120 ms delay added to compensate playhead latency
   ```

   Save.

5. **Restart the song**. Hot-reload picks up the new offset within a few seconds. Re-observe: did the visual move into sync with the music? Adjust further if needed.

6. **Iterate** until you can't tell the visual from the music. Usually 2-3 rounds gets you within ±20 ms.

### Per-player vs per-track

The offset measurement should converge on a value that's stable for the **player** (Apple Music, Spotify desktop, VLC, etc.). Suspect a constant per-player value if every track you measure lands at the same offset.

When the offset shifts between tracks on the SAME player, it's usually a **master difference** — the audio file has different intro silence, different mastering padding, etc. compared to what librosa analysed. In that case the offset is genuinely per-track.

Bench observations to date (extend this table as you measure):

| Player | Platform | Typical `@offset` |
|---|---|---|
| Apple Music desktop | macOS arm64 | (TBD — bench measurement pending) |
| Spotify desktop | macOS arm64 | (TBD) |
| VLC | macOS arm64 | (TBD) |
| Tidal desktop | macOS arm64 | (TBD) |

When this table fills in with stable per-player values, a future feature could store the offset against the player ID and apply automatically — eliminating the per-track hand-tune for the constant component.

---

## Common gotchas

**Non-Latin lyrics render as boxes on the Tildagon.** Step 1 warns you; transliterate / translate the affected `BodyText:` lines. The StickC LCD is fine — Arimo isn't limiting there. See [`feedback_tildagon_font_latin_only`](../../memory/feedback_tildagon_font_latin_only.md) in project memory.

**librosa picks the half/double tempo.** Bench-listen the seeded sparkle cadence. If they feel manic or sluggish, edit `@bpm` by hand to the half/double value.

**The seed feels too busy / too sparse.** Tag `# seed` is your friend. Delete the lines you don't want; keep the wash anchors at section starts (they're usually fine) and add your own pulses / sparkles where they belong.

**Re-running step 2 wipes my section renames.** No, it doesn't — section renames carry across re-syncs via boundary-overlap matching. If the librosa analysis shifts a section boundary by less than ~2 s, the renamed name (e.g. `chorus2`) follows. Default-named sections (`section3`) get re-emitted afresh on each run.

**Apple Music DRM is silently encrypted.** Apple Music subscription downloads use FairPlay; they look like `.m4a` files but can't be decoded outside the Apple Music app. Get audio from a non-DRM source (Bandcamp, iTunes Store purchase, owned CD rip) for MIR analysis. The Apple Music PLAYER works fine for runtime; the offline tooling needs DRM-free input.

**The cue file looks long / overwhelming after `--seed`.** Try `--snap` without `--seed` first to get just the beat-quantised lyrics + clean section headers. Add `--seed` on a later run if the auto-FX scaffold helps.

---

## See also

- [`cue-file-schema.md`](cue-file-schema.md) — every directive, every cue-line kind.
- [`tools/README.md`](../tools/README.md) — per-tool CLI reference.
- [`feedback_tildagon_font_latin_only`](../../memory/feedback_tildagon_font_latin_only.md) — why non-Latin lyrics need romanising.
- Worked example: [`coldplay-viva-la-vida.cues`](../songs/coldplay-viva-la-vida.cues) — full Epic 14 pipeline output, hand-edited.
