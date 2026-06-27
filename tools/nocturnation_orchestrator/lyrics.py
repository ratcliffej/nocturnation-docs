"""LRC fetch + parse helpers, plus the cue-skeleton template renderer.

Uses lrclib.net's free, no-auth public API to pull synced lyrics for
a given (artist, title) pair. The skeleton generator turns the LRC
into a starter `.cues` file: file-level directives, one `# lyric` line
per stamped LRC line at its time, and blank rows in between marking
where the LD can drop FX cues.

This is an AUTHORING helper, not a runtime dep. The orchestrator
itself never hits lrclib.net.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass


LRCLIB_GET_URL = "https://lrclib.net/api/get"

_LRC_LINE_RE = re.compile(
    r"^\[(\d+):(\d+)(?:\.(\d{1,3}))?\](.*)$"
)


class LyricsError(Exception):
    """Raised when lrclib.net can't service a request."""


@dataclass
class LyricLine:
    time_ms: int
    text: str


def fetch_lrc(artist, title, *, http_get=None, timeout=10.0):
    """Fetch the LRC for (artist, title) from lrclib.net.

    Returns the synced lyric string (`[MM:SS.xx] line\n...`) or
    raises LyricsError if nothing matches.

    Args:
        artist (str): track artist; passed through urllib.parse.quote.
        title (str): track title.
        http_get (callable | None): injectable for tests. Signature
            (url, timeout) -> (status, body_bytes). Defaults to a
            urllib.request-based call.
        timeout (float): seconds before the HTTP call gives up.
    """
    if not artist or not title:
        raise LyricsError("artist and title are both required")
    if http_get is None:
        http_get = _default_http_get
    query = urllib.parse.urlencode({
        "artist_name": artist,
        "track_name": title,
    })
    url = "%s?%s" % (LRCLIB_GET_URL, query)
    status, body = http_get(url, timeout)
    if status == 404:
        raise LyricsError(
            "lrclib.net has no entry for %s / %s" % (artist, title)
        )
    if status != 200:
        raise LyricsError(
            "lrclib.net returned HTTP %d for %s / %s" % (status, artist, title)
        )
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise LyricsError("lrclib.net response was not JSON: %s" % exc)
    lrc = data.get("syncedLyrics")
    if not lrc:
        # Some tracks only have plainLyrics. Without timestamps the
        # skeleton has nowhere to anchor lines; treat as a miss so the
        # author knows the LRC is partial.
        raise LyricsError(
            "lrclib.net entry for %s / %s has no synced lyrics"
            % (artist, title)
        )
    return lrc


def _default_http_get(url, timeout):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "nocturnation-gen-cues/1 (https://nocturnation.com)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except urllib.error.URLError as exc:
        raise LyricsError("network error contacting lrclib.net: %s" % exc) from None


def parse_lrc(lrc_text):
    """Parse LRC text into a list of LyricLine, sorted by time.

    Tolerant of:
      - blank / metadata lines (e.g. `[ti:Title]`, `[ar:Artist]`)
      - lines with multiple timestamps (rare; treated as one line per
        timestamp)
      - missing centisecond field (`[01:23]` instead of `[01:23.45]`)
    """
    lines = []
    for raw in lrc_text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        # Find every timestamp prefix on this line; LRC sometimes
        # repeats one line under several stamps.
        stamps = re.findall(r"\[(\d+):(\d+)(?:\.(\d{1,3}))?\]", raw)
        text = re.sub(r"\[\d+:\d+(?:\.\d{1,3})?\]", "", raw).strip()
        if not stamps:
            continue
        for mm, ss, cc in stamps:
            mm_i = int(mm)
            ss_i = int(ss)
            cc_str = (cc or "").ljust(3, "0")
            cc_i = int(cc_str) if cc_str else 0
            time_ms = (mm_i * 60 + ss_i) * 1000 + cc_i
            # Skip metadata-style lines: `[ar:Artist]`, `[ti:Title]`,
            # etc., which the timestamp regex matches harmlessly.
            if not text:
                continue
            lines.append(LyricLine(time_ms=time_ms, text=text))
    # Metadata lines like `[ti:Title]` parse as 'ti'/'Title' tokens
    # under the numeric regex - filter those by checking the matched
    # `mm` was actually digits (the regex enforces that), so the only
    # invalid entries are the empty-text ones we already skipped.
    lines.sort(key=lambda l: l.time_ms)
    return lines


def render_skeleton(artist, title, lyric_lines, *,
                    default_bpm=120, default_fx="quiet_wash",
                    default_fx_params="20 40 80",
                    comment_anchors=False):
    """Render a starter `.cues` file as a string.

    By default (Epic 14 B1, lyric-first authoring): each lyric line
    becomes a real ``BodyText:`` cue at its stamped time, with
    centisecond precision preserved from the LRC source. Lyrics
    render on Lume LCDs immediately - no manual conversion needed.

    Legacy mode (``comment_anchors=True``): each lyric becomes a
    ``#`` comment instead. Pre-Epic-13 behaviour, preserved for
    the rare case where the author wants to add lyrics as cues
    themselves.

    Detected non-Latin scripts in the lyric text trigger a single
    `#` comment block warning the author that the relevant lines
    need transliteration before they'll render on the Tildagon
    (see [[tildagon-font-latin-only]] in project memory).
    """
    out = []
    out.append("# %s - %s" % (artist, title))
    out.append("#")
    out.append("# Skeleton generated from lrclib.net synced lyrics.")
    if comment_anchors:
        out.append("# The lyric rows are time anchors; add real cue")
        out.append("# lines between them to choreograph the show.")
    else:
        out.append("# Lyrics emitted as BodyText: cues; add FX cues")
        out.append("# alongside them to choreograph the show. Re-run")
        out.append("# audio_enrich_cues.py to add librosa MIR data.")
    out.append("")

    # Non-Latin script detection. Single up-front warning lets the
    # author skim once + know which lines need romanising rather than
    # discovering it at bench-test time.
    scripts = detect_non_latin_scripts(
        "\n".join(l.text for l in lyric_lines)
    )
    if scripts:
        out.append(
            "# WARNING: lyrics contain non-Latin script(s): %s."
            % ", ".join(sorted(scripts))
        )
        out.append("# These will render as missing-glyph boxes on the")
        out.append("# Tildagon (Arimo font is Latin-only). Romanise or")
        out.append("# translate the affected BodyText: lines before show.")
        out.append("")

    out.append("@artist     %s" % artist)
    out.append("@title      %s" % title)
    out.append("@bpm        %d" % default_bpm)
    out.append("@default_fx %s %s" % (default_fx, default_fx_params))
    out.append("")

    if not lyric_lines:
        out.append("# (no synced lyrics found)")
        out.append("")
        return "\n".join(out) + "\n"

    if comment_anchors:
        for line in lyric_lines:
            stamp = _fmt_time(line.time_ms, with_centiseconds=False)
            out.append("# %s  %s" % (stamp, line.text))
    else:
        for line in lyric_lines:
            stamp = _fmt_time(line.time_ms, with_centiseconds=True)
            out.append("%s  BodyText: %s" % (stamp, line.text))
    out.append("")
    return "\n".join(out) + "\n"


def _fmt_time(time_ms, *, with_centiseconds=True):
    """Format a millisecond time as ``MM:SS`` or ``MM:SS.cc``.

    Centisecond precision matches the cue file schema's preferred
    format for cue line timestamps + preserves the source LRC's
    precision rather than truncating it.
    """
    total_seconds = time_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    if not with_centiseconds:
        return "%02d:%02d" % (minutes, seconds)
    cs = (time_ms % 1000) // 10   # centiseconds (00..99)
    return "%02d:%02d.%02d" % (minutes, seconds, cs)


# ---------------------------------------------------------------------------
# Non-Latin script detection (Epic 14 B1)
# ---------------------------------------------------------------------------
#
# The Tildagon's bundled Ctx font is Arimo Regular - Latin-only. Hangul
# / kana / kanji / Cyrillic / Greek / Arabic / Devanagari etc. render
# as missing-glyph boxes on the LCD. Detect at authoring time so the
# author can romanise / translate before the show.
#
# Detection is intentionally coarse: Unicode block ranges, not full
# script analysis. Good enough to flag "you have Hangul here" without
# overreaching into rare-block edge cases.

_SCRIPT_BLOCKS = (
    # name             start     end (inclusive)
    ("Hangul",         0xAC00,   0xD7AF),   # Hangul Syllables
    ("Hangul",         0x1100,   0x11FF),   # Hangul Jamo
    ("Hiragana",       0x3040,   0x309F),
    ("Katakana",       0x30A0,   0x30FF),
    ("CJK",            0x4E00,   0x9FFF),   # CJK Unified Ideographs
    ("Cyrillic",       0x0400,   0x04FF),
    ("Greek",          0x0370,   0x03FF),
    ("Arabic",         0x0600,   0x06FF),
    ("Hebrew",         0x0590,   0x05FF),
    ("Devanagari",     0x0900,   0x097F),
    ("Thai",           0x0E00,   0x0E7F),
)


def detect_non_latin_scripts(text):
    """Return the set of non-Latin script names appearing in ``text``.

    Empty set if ``text`` is pure Latin (Basic Latin, Latin-1
    supplement, Latin Extended-A/B). Hangul / kana / CJK etc. each
    contribute their script name. A line of mixed English + Korean
    returns ``{"Hangul"}``.
    """
    found = set()
    for ch in text:
        cp = ord(ch)
        if cp < 0x0250:
            # Basic Latin + Latin-1 supplement + Latin Extended-A
            # (covers all standard European diacritics).
            continue
        for name, lo, hi in _SCRIPT_BLOCKS:
            if lo <= cp <= hi:
                found.add(name)
                break
    return found
