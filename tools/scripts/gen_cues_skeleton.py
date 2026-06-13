#!/usr/bin/env python3
"""Generate a `.cues` skeleton from lrclib.net synced lyrics.

Usage::

    Docs/tools/scripts/gen_cues_skeleton.py "Coldplay" "Fix You"
    Docs/tools/scripts/gen_cues_skeleton.py "Coldplay" "Fix You" --stdout
    Docs/tools/scripts/gen_cues_skeleton.py "Coldplay" "Fix You" \\
        --output /tmp/draft.cues

Default behaviour writes to `Docs/songs/<slug>.cues`, where slug is
the same one the orchestrator's track matcher will use. Refuses to
overwrite by default; pass `--force` to replace.

Network: pulls one HTTP GET from https://lrclib.net (free, no auth).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR))

from nocturnation_orchestrator.lyrics import (
    LyricsError, fetch_lrc, parse_lrc, render_skeleton,
)
from nocturnation_orchestrator.matcher import slugify


_DEFAULT_SONGS_DIR = _TOOLS_DIR.parent / "songs"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate a .cues skeleton from synced lyrics on lrclib.net",
    )
    parser.add_argument("artist", help="track artist (e.g. \"Coldplay\")")
    parser.add_argument("title",  help="track title  (e.g. \"Fix You\")")
    parser.add_argument(
        "--songs-dir", default=str(_DEFAULT_SONGS_DIR),
        help="destination directory (default: %(default)s)",
    )
    parser.add_argument(
        "--output", default=None,
        help="explicit output path (default: <songs-dir>/<slug>.cues)",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="emit to stdout instead of writing a file",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="overwrite an existing file at the target path",
    )
    parser.add_argument(
        "--bpm", type=int, default=120,
        help="@bpm directive value (default: %(default)s; update by hand)",
    )
    parser.add_argument(
        "--default-fx", default="quiet_wash",
        help="@default_fx cue_name (default: %(default)s)",
    )
    parser.add_argument(
        "--default-fx-params", default="20 40 80",
        help="@default_fx positional params (default: \"%(default)s\")",
    )
    args = parser.parse_args(argv)

    try:
        lrc = fetch_lrc(args.artist, args.title)
    except LyricsError as exc:
        sys.exit("error: %s" % exc)

    lines = parse_lrc(lrc)
    if not lines:
        sys.exit("error: lrclib.net returned LRC but it parsed to zero lines")

    body = render_skeleton(
        args.artist, args.title, lines,
        default_bpm=args.bpm,
        default_fx=args.default_fx,
        default_fx_params=args.default_fx_params,
    )

    if args.stdout:
        sys.stdout.write(body)
        return

    if args.output:
        target = Path(args.output)
    else:
        slug = slugify(args.artist, args.title)
        if not slug:
            sys.exit("error: artist + title slug is empty; pass --output explicitly")
        target = Path(args.songs_dir) / ("%s.cues" % slug)

    if target.exists() and not args.force:
        sys.exit(
            "error: %s already exists; pass --force to overwrite" % target
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    print("wrote %s (%d lyric lines, %d bytes)" % (target, len(lines), len(body)))


if __name__ == "__main__":
    main()
