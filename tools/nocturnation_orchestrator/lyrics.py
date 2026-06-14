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
                    default_fx_params="20 40 80"):
    """Render a starter `.cues` file as a string.

    Each lyric line becomes a `#` comment at its stamped time. The
    LD adds real cue lines between the anchors by hand - the lyric
    comments orient them in the song; the actual cue authoring is
    the creative act and shouldn't be auto-pre-stubbed with TODO
    placeholders that pile up as noise.
    """
    out = []
    out.append("# %s - %s" % (artist, title))
    out.append("#")
    out.append("# Skeleton generated from lrclib.net synced lyrics.")
    out.append("# The lyric rows are time anchors; add real cue")
    out.append("# lines between them to choreograph the show.")
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

    for line in lyric_lines:
        stamp = _fmt_time(line.time_ms)
        out.append("# %s  %s" % (stamp, line.text))
    out.append("")
    return "\n".join(out) + "\n"


def _fmt_time(time_ms):
    total_seconds = time_ms // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return "%02d:%02d" % (minutes, seconds)
