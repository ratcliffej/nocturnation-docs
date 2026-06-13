"""Track -> cue file matcher.

Maps a (artist, title) pair from the now-playing backend to a
`.cues` file in the songs directory. The match is a normalised slug
lookup: case-folded, punctuation-stripped, words joined with '-'.

Examples:
    "Coldplay" + "Fix You"        -> "coldplay-fix-you.cues"
    "The Cure" + "Pictures Of You" -> "the-cure-pictures-of-you.cues"

If no slug match exists, fall back to `_default.cues`. If THAT
doesn't exist either, the matcher returns None and the caller goes
ambient (silence / no FX).

Why slug instead of fuzzy match: slug is deterministic, debuggable
("rename the file to <X>"), and trivially extended later if needed.
Fuzzy match (Levenshtein on artist+title) would help with
ID3-spelling drift but adds runtime variability that's hard to
diagnose from a single-line orchestrator log.
"""

import re
import unicodedata
from pathlib import Path


def slugify(*parts):
    """Lower-case, ASCII-fold, replace non-alphanumeric runs with '-'.

    Joins the supplied parts with '-' (e.g. artist + title).
    """
    pieces = []
    for part in parts:
        if not part:
            continue
        # NFKD-fold accents -> ASCII; drop combining marks.
        norm = unicodedata.normalize("NFKD", str(part))
        ascii_only = norm.encode("ascii", "ignore").decode("ascii")
        ascii_only = ascii_only.lower()
        ascii_only = re.sub(r"[^a-z0-9]+", "-", ascii_only)
        ascii_only = ascii_only.strip("-")
        if ascii_only:
            pieces.append(ascii_only)
    return "-".join(pieces)


def find_cue_path(songs_dir, artist, title):
    """Return the matching `.cues` path, the `_default.cues` fallback,
    or None if neither exists.

    Args:
        songs_dir (str | Path): directory holding `.cues` files.
        artist (str): from now-playing backend.
        title (str): from now-playing backend.
    """
    songs_dir = Path(songs_dir)
    slug = slugify(artist, title)
    if slug:
        per_track = songs_dir / ("%s.cues" % slug)
        if per_track.is_file():
            return per_track
    default = songs_dir / "_default.cues"
    if default.is_file():
        return default
    return None
