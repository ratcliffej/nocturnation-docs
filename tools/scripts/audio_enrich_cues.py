#!/usr/bin/env python3
"""Enrich a `.cues` file with librosa MIR data (Epic 14 B2).

Step 2 of the lyric-first authoring flow. Takes an existing cue
file (typically produced by `cues_from_lyrics.py`) + an audio file,
runs librosa beat-tracking + section segmentation + key estimation,
and rewrites the cue file's header block with the detected tempo,
key, mode, duration, and section directives. Hand-edited body cues
are preserved verbatim.

Idempotent: re-running with the same audio + cue file produces
byte-identical output (modulo the `@analysis_synced` timestamp).
Re-running with a re-mastered audio file updates the header +
re-detects sections; author-renamed sections are preserved by
boundary-overlap matching.

Usage::

    Docs/tools/scripts/audio_enrich_cues.py \\
        Docs/songs/coldplay-fix-you.cues \\
        --audio /path/to/fix-you.mp3

    # Print to stdout instead of rewriting the file:
    audio_enrich_cues.py Docs/songs/x.cues --audio x.mp3 --stdout

    # Skip the sidecar JSON write (header-only output):
    audio_enrich_cues.py x.cues --audio x.mp3 --no-sidecar

Outputs:
    - The cue file at `<cuefile>` is rewritten in place (atomic).
    - A sidecar `<cuefile>.analysis.json` holds the full librosa
      dump (beats, onsets, chroma summary, full sections array,
      etc.). Gitignored. Used by future tools (--snap, --seed)
      that need beat-level data.

Requirements:
    pip install librosa
    brew install ffmpeg    # macOS; needed for .mp3 decode

Network: none. Pure local audio analysis.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from nocturnation_orchestrator import cue_rewrite, mir


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Enrich a .cues file with librosa MIR data "
                    "(tempo, key, mode, sections).",
    )
    parser.add_argument("cuefile", help="path to the .cues file to enrich")
    parser.add_argument(
        "--audio", default=None,
        help=(
            "path to the audio file (flac / wav / mp3 / m4a / ogg). "
            "Optional: if omitted, the tool looks for a file with the "
            "same base name as the cue file in the same directory "
            "(flac preferred over mp3 etc. for MIR quality)."
        ),
    )
    parser.add_argument(
        "--update", action="store_true",
        help=(
            "Mark this run as a re-sync. Behaviour is currently the "
            "same as a first-time enrichment (auto-discover audio + "
            "make .bak backup); the flag exists for discoverability."
        ),
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="Skip the .cues.bak backup before rewriting.",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="print the rewritten cue file to stdout instead of writing",
    )
    parser.add_argument(
        "--no-sidecar", action="store_true",
        help="skip writing the .cues.analysis.json sidecar",
    )
    parser.add_argument(
        "--schema-version", type=int, default=1,
        help="value for @analysis_version (default: 1)",
    )
    parser.add_argument(
        "--snap", action="store_true",
        help=(
            "Snap existing cue-line timestamps to the nearest detected "
            "beat (within --snap-threshold-ms). Useful when lyric "
            "timestamps from lrclib are loose against the beat grid, "
            "or when hand-typed cues want quantising to the music."
        ),
    )
    parser.add_argument(
        "--snap-threshold-ms", type=int, default=150, metavar="N",
        help=(
            "Max gap to snap a cue to a beat, in milliseconds "
            "(default: %(default)s). Cues outside this threshold are "
            "left at their authored time."
        ),
    )
    parser.add_argument(
        "--seed", action="store_true",
        help=(
            "Emit a first-pass FX seed: a # seed-tagged quiet_wash "
            "at each section start (colour from key + mode) plus a "
            "sparkle_on_beat for above-median-loudness sections. "
            "Grep '# seed' to find or bulk-delete."
        ),
    )
    args = parser.parse_args(argv)

    cuefile = Path(args.cuefile)

    if args.audio:
        audio = Path(args.audio)
        if not audio.exists():
            sys.exit("error: audio file not found: %s" % audio)
    else:
        # Auto-discover: look in the cue file's directory for an
        # audio file with the same base name.
        discovered = cue_rewrite.discover_audio(cuefile)
        if discovered is None:
            sys.exit(
                "error: no --audio supplied and auto-discovery found no "
                "audio file alongside %s "
                "(tried .flac/.wav/.aiff/.aif/.m4a/.mp3/.ogg). "
                "Pass --audio <path> explicitly." % cuefile
            )
        audio = discovered
        print("auto-discovered audio: %s" % audio, file=sys.stderr)

    # Read the existing cue file if it exists; treat absence as "empty
    # input" so the tool can also do first-time enrichment of a track
    # with no lyric-first skeleton (rare but legal).
    if cuefile.exists():
        content = cuefile.read_text()
    else:
        content = ""

    print("running librosa analysis on %s..." % audio, file=sys.stderr)
    try:
        analysis = mir.analyse(audio)
    except ImportError as exc:
        sys.exit(
            "error: librosa not installed (%s).\n"
            "       Install with: pip install librosa  +  brew install ffmpeg"
            % exc
        )
    except Exception as exc:                   # noqa: BLE001
        sys.exit("error: librosa analysis failed: %s" % exc)

    print(
        "  tempo=%.1f bpm  key=%s %s  duration=%.1fs  sections=%d" % (
            analysis["tempo"], analysis["key"], analysis["mode"],
            analysis["duration_s"], len(analysis["sections"]),
        ),
        file=sys.stderr,
    )

    # Beat snapping runs FIRST (body cues only); header rewrite runs
    # AFTER (replaces the header zone). Order: cues_from_lyrics.py
    # produced a body of BodyText: cues at LRC-derived timestamps -
    # snap those onto the beat grid, then the header rewrite captures
    # the result + adds the MIR directives.
    if args.snap:
        content, snap_stats = cue_rewrite.snap_cue_timestamps(
            content, analysis["beats"],
            threshold_ms=args.snap_threshold_ms,
        )
        print(
            "  snap: %d cues snapped, %d kept outside threshold "
            "(max delta %.0f ms)" % (
                snap_stats["snapped"], snap_stats["kept"],
                snap_stats["max_delta_ms"],
            ),
            file=sys.stderr,
        )

    # FX seeding runs after snap (so seeded cues land on beat-snapped
    # timestamps when applicable) and before the header rewrite (so
    # the rewriter's body-preservation logic picks them up alongside
    # any existing body cues).
    if args.seed:
        content, seed_stats = cue_rewrite.seed_fx_cues(content, analysis)
        print(
            "  seed: %d wash + %d sparkle cues emitted "
            "(%d sections skipped, too short)" % (
                seed_stats["wash_cues"], seed_stats["sparkle_cues"],
                seed_stats["skipped"],
            ),
            file=sys.stderr,
        )

    new_content = cue_rewrite.rewrite_cue_file(
        content, analysis, schema_version=args.schema_version,
    )

    if args.stdout:
        sys.stdout.write(new_content)
        return

    # Make a .bak backup before rewriting so a corrupted run can't
    # eat the operator's hand-edits. No-op if the cue file doesn't
    # exist yet (first-time enrichment); skip on --no-backup.
    if not args.no_backup:
        backup_path = cue_rewrite.make_backup(cuefile)
        if backup_path is not None:
            print("backup: %s" % backup_path, file=sys.stderr)

    # Write the cue file atomically.
    cuefile.parent.mkdir(parents=True, exist_ok=True)
    tmp = cuefile.with_suffix(cuefile.suffix + ".tmp")
    tmp.write_text(new_content)
    tmp.replace(cuefile)
    print("wrote %s" % cuefile, file=sys.stderr)

    if not args.no_sidecar:
        sidecar = cuefile.with_suffix(cuefile.suffix + ".analysis.json")
        sidecar.write_text(json.dumps(analysis, indent=2))
        print("wrote %s (sidecar)" % sidecar, file=sys.stderr)


if __name__ == "__main__":
    main()
