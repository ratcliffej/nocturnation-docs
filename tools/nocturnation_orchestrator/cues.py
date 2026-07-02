"""Cue-file parser.

Reads `.cues` files (one cue per line, whitespace separated) and emits
a `CueFile` ready for the orchestrator main loop to walk.

Grammar (informal):

    file        := (directive | cue | blank | comment)*
    directive   := '@' name token*
    cue         := time (fx_cue | display_cue)
    fx_cue      := fx_token positional* flag*
    display_cue := 'HeaderText:' text
                 | 'BodyText:' text
                 | 'clearscreen'
    fx_token    := cue_name | 'stop' | 'bpm'
    positional  := integer
    flag        := '--' name integer
    time        := MM:SS | M:SS | H:MM:SS | HH:MM:SS
    comment     := '#' .* end-of-line  (allowed anywhere; truncates the line)

Recognised directives:

    @bpm           N            file-default BPM
    @default_fx    name [params] FX that runs before first cue / after `stop`
    @artist        free text    label used by the matcher
    @title         free text    label used by the matcher
    @offset        seconds      whole-file time shift (positive delays)
    @ShowSongInfo  [bool]       Epic 13: emit a now-playing TEXT_DISPLAY
                                 card (header=title, body=artist) on track
                                 start. Default off. Bare directive = on.
    @ShowBitmap    [bool]       Epic 13: reserved for B5+ (now-playing bitmap
                                 on track start). Parsed but no-op in B4.

Recognised flags (FX cues only):

    --bpm       N               per-cue BPM override (FX-engine knob)
    --buildup   N               per-cue buildup_s override

Display cue lines (Epic 13):

    MM:SS HeaderText: <text>    set the header line (single line, large
                                 font; over-width content marquee-scrolls
                                 on the Lume). Empty after colon = clear
                                 just the header field.
    MM:SS BodyText: <text>      set the body (word-wrapped on the Lume,
                                 smaller font). Empty = clear body.
    MM:SS clearscreen           clear both text and bitmap layers.

Header / body state composes: the orchestrator state-tracks the
current (header, body) and emits a TEXT_DISPLAY frame whenever either
field changes via a HeaderText: / BodyText: cue. Sticky on the Lume
until superseded by another TEXT_DISPLAY or cleared by clearscreen.

Positional params map to the FX's PARAMS declaration in order,
skipping reserved (None-named) slots. The cue file always carries
HUMAN values; the parser converts via convert_to_u8 to the 6-u8 tuple
the FX's start() consumes.

Errors carry the line number for debuggability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .fx.params import convert_to_u8
from .fx.registry import fx_registry


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class CueParseError(Exception):
    """Raised on any malformed cue file content."""

    def __init__(self, msg: str, line_no: Optional[int] = None):
        self.line_no = line_no
        if line_no is not None:
            msg = "line %d: %s" % (line_no, msg)
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class Cue:
    """One scheduled cue.

    Most cues are FX admissions (kind="fx"). The "bpm" kind is a
    meta cue that mutates the file-level default BPM mid-track and
    fires no FX; it's used for tracks with tempo changes. Epic 13
    adds three display-content kinds: "header_text", "body_text",
    "clearscreen" - these fire the orchestrator's TEXT_DISPLAY /
    CLEAR_SCREEN emitter and do not touch the FX runner. All kinds
    share the same dataclass to keep the timeline a single sorted
    sequence.

    Attributes:
        time_ms (int): absolute time into the track, in milliseconds.
        kind (str): "fx" (default), "bpm", "header_text",
            "body_text", or "clearscreen".
        fx_id (int): registry id (kind="fx" only); 0 = `stop` cue.
        params (tuple[int, ...]): u8 params for FX.start(); length
            matches the FX's PARAMS declaration. Reserved slots are 0.
        params_raw (tuple[int, ...]): human-supplied values BEFORE
            unit conversion (e.g. probability 30 stays as 30 here,
            even though params[i] becomes 76 after percent->u8). Used
            for debug log lines so the LD sees what they wrote.
        bpm (int): for kind="fx" - per-cue BPM override (0 = inherit).
            For kind="bpm" - the new file-level default BPM to apply.
        buildup_s (int): per-cue buildup window (kind="fx" only).
        text (str): for kind="header_text" / "body_text" - the
            string payload. Empty string clears that field.
        line_no (int): source line for diagnostics.
    """
    time_ms: int
    kind: str = "fx"
    fx_id: int = 0
    params: tuple = ()
    params_raw: tuple = ()
    bpm: int = 0
    buildup_s: int = 0
    text: str = ""
    line_no: int = 0


@dataclass
class Lyric:
    """A timestamped lyric anchor lifted from a `# MM:SS text` comment.

    Authoring helper output (`gen_cues_skeleton.py`) already emits
    lyrics in this shape, so the cue parser can recover them with
    zero changes to the file format. Lyrics are advisory: the
    scheduler logs them in debug mode but they have no effect on
    the running FX.
    """
    time_ms: int
    text: str
    line_no: int = 0


@dataclass
class CueFile:
    """Parsed cue file.

    cues is sorted by time_ms ascending. default_fx_* is the FX (if any)
    the orchestrator runs before the first cue and after a `stop`.
    lyrics is sorted by time_ms too; populated from timestamped
    `# MM:SS text` comments. Empty unless the file uses lyric anchors.
    offset_ms is the per-file shift applied to every cue and lyric
    time at parse time (positive delays everything, negative pulls
    forward). Used to compensate for album-mastering silence padding
    when the same song is shipped with different intros across
    releases.

    Epic 13 fields:
        show_song_info (bool): if set, the orchestrator emits a
            TEXT_DISPLAY now-playing card (header=title, body=artist)
            on track start. Intended for the default cue file (when
            no song-specific .cues exists). Song-specific files use
            explicit HeaderText: / BodyText: cue lines instead.
        show_bitmap (bool): if set, the orchestrator emits a generic
            now-playing bitmap on track start. Reserved for B5+;
            parsed but no-op in B4.
    """
    artist: str = ""
    title: str = ""
    default_bpm: int = 0
    default_fx_id: int = 0
    default_fx_params: tuple = (0, 0, 0, 0, 0, 0)
    cues: list = field(default_factory=list)
    lyrics: list = field(default_factory=list)
    offset_ms: int = 0
    show_song_info: bool = False
    show_bitmap: bool = False

    # Epic 14 MIR-enrichment metadata. Set by audio_enrich_cues.py.
    # Captured for diagnostics + future use (see B7); the orchestrator
    # doesn't act on these at runtime today. Empty strings / 0 / None
    # when the file hasn't been MIR-enriched yet.
    time_sig: int = 0
    key: str = ""
    mode: str = ""
    duration_ms: int = 0
    sections: list = field(default_factory=list)
    analysis_synced: str = ""
    analysis_version: int = 0
    analysis_tool: str = ""

    # Epic 14 B7 authoring shortcuts:
    #   palettes: name -> [(r, g, b), ...] colour sets declared via
    #     @palette. Parser captures; expansion to RGB token triplets
    #     in body cue args lives in `_expand_palette_placeholders`.
    #   pending_during: collected during parse; resolved against
    #     `sections` at the end of parse_cues() into real cues. Empty
    #     after parse completes; only here for the parse-time pass.
    palettes: dict = field(default_factory=dict)
    pending_during: list = field(default_factory=list)

    # Epic 14.9 Block B. Beat positions in milliseconds, loaded from
    # the `<basename>.cues.analysis.json` sidecar by parse_cues_file()
    # when one exists. Empty list when no sidecar is present (the
    # FX layer falls back to its bpm-derived clock). Sorted ascending.
    beats_ms: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d{2})(?:\.(\d{1,3}))?$")
_INT_RE = re.compile(r"^-?\d+$")

# `# MM:SS[.xxx] text` (or `# H:MM:SS[.xxx] text`). Used for lyric-anchor
# extraction; lines that match become Lyric entries instead of being
# discarded as comments.
#
# Whitespace after `#` is REQUIRED. `#00:45 drift_wash ...` (no space)
# does NOT match - the LD's natural way to comment out a cue
# temporarily is to prepend `#` with no space, and we should leave
# that alone instead of treating the cue body as lyric text.
_LYRIC_COMMENT_RE = re.compile(
    r"^#\s+((?:\d+:)?\d+:\d{2}(?:\.\d{1,3})?)\s+(.+?)\s*$"
)


def _parse_time(token: str, line_no: int) -> int:
    """Parse MM:SS / M:SS / H:MM:SS, optionally with .x / .xx / .xxx
    fractional seconds, to milliseconds.

    Fractional precision: 1-3 digits after the dot, interpreted as
    left-aligned milliseconds (".5" -> 500 ms, ".05" -> 50 ms,
    ".005" -> 5 ms). Matches LRC centisecond grain so LD can paste
    lyric timestamps straight in.
    """
    m = _TIME_RE.match(token)
    if not m:
        raise CueParseError(
            "invalid time %r (expected MM:SS, H:MM:SS, or MM:SS.xxx)"
            % token,
            line_no,
        )
    h = int(m.group(1) or 0)
    minutes = int(m.group(2))
    secs = int(m.group(3))
    frac = m.group(4) or ""
    if secs >= 60:
        raise CueParseError("seconds field >= 60 in %r" % token, line_no)
    total_ms = ((h * 60 + minutes) * 60 + secs) * 1000
    if frac:
        total_ms += int(frac.ljust(3, "0"))
    return total_ms


def _parse_int(token: str, line_no: int, what: str = "integer") -> int:
    if not _INT_RE.match(token):
        raise CueParseError("expected %s, got %r" % (what, token), line_no)
    return int(token)


def _parse_hex_colour(token: str):
    """Parse '#RRGGBB' (or 'RRGGBB') into an (r, g, b) tuple.

    Returns None on malformed input - palette declarations are
    forgiving (one typo shouldn't break the file; the broken entry
    just doesn't make it into the palette). Used by the @palette
    directive handler.
    """
    if not token:
        return None
    s = token[1:] if token.startswith("#") else token
    if len(s) != 6:
        return None
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return (r, g, b)
    except ValueError:
        return None


def _strip_comment(line: str) -> str:
    """Drop everything from the first comment-starting '#'.

    A '#' counts as a comment marker when EITHER:
      * It's the first non-whitespace character on the line, OR
      * It's mid-line AND preceded by whitespace AND followed by
        whitespace (or end-of-line).

    Otherwise '#' is treated as a literal character (key names like
    'A#', hex colour values like '#FF0000', kwargs like 'colour=#F8').

    Stripped (treated as comments):
      # foo                  start-of-line # then space
      #foo                   start-of-line # then non-space (matches
                              the `#00:45 cue_line_here` "comment out"
                              authoring convention)
      ... # seed             mid-line # surrounded by whitespace
      #                      lone # at start of line

    Preserved (treated as literal):
      A#                     mid-line # preceded by non-whitespace
      @palette x #FF0000     mid-line # NOT followed by whitespace
      colour=#FF8800         neither neighbouring char is whitespace
    """
    n = len(line)
    # Find the index of the first non-whitespace character. -1 if the
    # line is entirely whitespace.
    first_non_ws = -1
    for j, c in enumerate(line):
        if not c.isspace():
            first_non_ws = j
            break

    for i, ch in enumerate(line):
        if ch != "#":
            continue
        # Case 1: # is the first non-whitespace char of the line.
        # Strip from here - matches the `#00:45 ...` "comment-out a
        # cue line" convention used throughout existing files.
        if i == first_non_ws:
            return line[:i]
        # Case 2: mid-line # bracketed by whitespace.
        prev_ws = line[i - 1].isspace()
        next_ws = (i + 1 >= n) or line[i + 1].isspace()
        if prev_ws and next_ws:
            return line[:i]
    return line


def _maybe_parse_lyric_comment(raw_line: str, line_no: int):
    """Return a Lyric if the raw line is a `# MM:SS text` comment, else None.

    Skeleton-generator placeholders (`# MM:SS  TODO: cue here`) are
    explicitly dropped so they don't appear in the debug lyric stream.
    Lines that look like timestamped comments but whose time fails to
    parse fall back to None (treated as ordinary comments by the
    caller).
    """
    stripped = raw_line.strip()
    if not stripped.startswith("#"):
        return None
    m = _LYRIC_COMMENT_RE.match(stripped)
    if not m:
        return None
    text = m.group(2).strip()
    if not text or text.upper().startswith("TODO"):
        return None
    try:
        time_ms = _parse_time(m.group(1), line_no)
    except CueParseError:
        return None
    return Lyric(time_ms=time_ms, text=text, line_no=line_no)


def _resolve_fx_by_name(name: str, line_no: int, registry) -> int:
    """Map a cue_name to its fx_id. Raises CueParseError on miss."""
    for fx_id in registry.all_ids():
        cls = registry.get(fx_id)
        if cls.cue_name == name:
            return fx_id
    raise CueParseError("unknown FX %r" % name, line_no)


# Epic 14.9 Block C. `Nb` or `N.5b` value suffix on any 100ms param
# means "N bars at the file's current bpm + time_sig". Computed at
# parse time so the FX layer stays bars-unaware. Reject the suffix
# loudly on non-100ms params - silent acceptance would let a typo
# (RGB '20b') sneak past as a degenerate-looking number.
_BARS_VALUE_RE = re.compile(r"^(\d+(?:\.\d+)?)b$")


def _bars_to_100ms_slider(bars: float, bpm: int, time_sig: int) -> int:
    """Convert N bars to the equivalent 100ms-slider value.

      bars * time_sig * (60000 / bpm) = total ms
      slider = round(total ms / 100)

    Clamped to 0..255 (the 100ms slider range; saturates beyond
    25.5 s rather than wrapping). Defensive defaults: bpm 0 or
    time_sig 0 fall back to 120 BPM and 4/4 so a file without those
    directives still parses (the operator gets approximately what
    they meant; explicit @bpm + @time_sig pin it exactly).
    """
    eff_bpm = bpm if bpm > 0 else 120
    eff_ts  = time_sig if time_sig > 0 else 4
    ms = bars * eff_ts * (60_000.0 / eff_bpm)
    slider = int(round(ms / 100.0))
    if slider < 0:   slider = 0
    if slider > 255: slider = 255
    return slider


def _build_params_tuple(fx_cls, positional: list, line_no: int, file=None):
    """Map a list of positional cue-file values to (params_u8, params_raw).

    params_u8   is the converted form the FX consumes via start().
    params_raw  is the user-supplied values (pre-conversion) so the
                debug log can show what they wrote (probability 30,
                not 76; pulse decay 5, not slider 128).

    Both tuples are sized to len(fx_cls.PARAMS); reserved slots stay
    0 in both. The caller's positional list must not exceed the
    count of non-reserved slots.

    Epic 14.9 Block C: a value token of shape `Nb` / `N.5b` is only
    valid on `100ms` params; converted to the equivalent slider
    using file.default_bpm + file.time_sig. file=None falls back
    to 120 BPM 4/4 (test-friendly default; production callers always
    pass file).
    """
    n = len(fx_cls.PARAMS)
    out = [0] * n
    raw = [0] * n
    named_slots = [
        (i, unit) for i, (name, unit, _desc) in enumerate(fx_cls.PARAMS)
        if name is not None
    ]
    if len(positional) > len(named_slots):
        raise CueParseError(
            "fx %s takes at most %d positional params, got %d"
            % (fx_cls.cue_name, len(named_slots), len(positional)),
            line_no,
        )
    bpm = file.default_bpm if file is not None else 0
    time_sig = file.time_sig if file is not None else 0
    for value_token, (slot, unit) in zip(positional, named_slots):
        bars_match = _BARS_VALUE_RE.match(value_token)
        if bars_match is not None:
            if unit != "100ms":
                raise CueParseError(
                    "'%s' (bars) is only valid on 100ms params; "
                    "fx %s param %d (unit %s) expects an integer"
                    % (
                        value_token, fx_cls.cue_name, slot,
                        unit if unit is not None else "?",
                    ),
                    line_no,
                )
            bars = float(bars_match.group(1))
            slider = _bars_to_100ms_slider(bars, bpm, time_sig)
            raw[slot] = value_token   # preserve "4b" in the raw log
            out[slot] = slider
            continue
        value = _parse_int(value_token, line_no, "param value")
        raw[slot] = value
        try:
            out[slot] = convert_to_u8(value, unit)
        except ValueError as exc:
            raise CueParseError(str(exc), line_no) from None
    return tuple(out), tuple(raw)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_KNOWN_FLAGS = ("--bpm", "--buildup")
_KNOWN_DIRECTIVES = (
    "@bpm", "@default_fx", "@artist", "@title", "@offset",
    # Epic 13 display directives.
    "@ShowSongInfo", "@ShowBitmap",
    # Epic 14 MIR-enrichment directives (B7 - parser acceptance).
    # Captured into CueFile fields for diagnostics + future use;
    # the orchestrator doesn't yet act on them at runtime.
    "@time_sig", "@key", "@mode", "@duration", "@section",
    "@analysis_synced", "@analysis_version", "@analysis_tool",
    # Epic 14 B7-rest authoring shortcuts.
    # @during expands to a real cue at parse-end (deferred until all
    # @section directives have been seen). @palette captures named
    # colour lists but doesn't yet auto-expand references in body
    # cue args (deferred to a future per-FX colour-arg pass).
    "@during", "@palette",
)
_FLOAT_RE = re.compile(r"^-?\d+(\.\d+)?$")

# Epic 14.9 Block A. A `@name[idx]` token in any position (cue-line
# colour arg, @during arg, etc.) expands at parse time to the three
# RGB tokens of the named palette's idx-th colour. The palette must
# be declared via `@palette` somewhere in the same file; ordering
# is irrelevant (a two-pass scan picks up declarations before
# placeholder expansion runs).
_PALETTE_PLACEHOLDER_RE = re.compile(
    r"^@([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$"
)

# Epic 13 display cue-line keywords. Recognised when they appear as
# the SECOND token on a cue line (after the time). Trailing colon is
# part of the keyword; the rest of the line is the text payload.
_DISPLAY_KEYWORDS = ("HeaderText:", "BodyText:", "clearscreen")


def _parse_float(token: str, line_no: int, what: str) -> float:
    if not _FLOAT_RE.match(token):
        raise CueParseError("expected %s, got %r" % (what, token), line_no)
    return float(token)


def _parse_directive(tokens: list, line_no: int, file: CueFile, registry) -> None:
    directive = tokens[0]
    if directive not in _KNOWN_DIRECTIVES:
        raise CueParseError("unknown directive %r" % directive, line_no)
    # Most directives need at least one argument; the two Epic 13
    # boolean directives (@ShowSongInfo / @ShowBitmap) accept zero
    # arguments as a "set to True" shorthand. Per-directive arg
    # validation runs inside each branch.
    if (len(tokens) < 2
            and directive not in ("@ShowSongInfo", "@ShowBitmap")):
        raise CueParseError("%s needs an argument" % directive, line_no)

    if directive == "@bpm":
        if len(tokens) != 2:
            raise CueParseError("@bpm takes one argument", line_no)
        file.default_bpm = _parse_int(tokens[1], line_no, "BPM")
    elif directive == "@offset":
        # Whole-file time shift in seconds. Accepts fractional and
        # negative values; converted to ms and applied at parse time
        # to every cue and lyric. Positive offsets DELAY (use when the
        # album has silence padding before the audible content);
        # negative offsets ADVANCE (rare - audio was authored against
        # a longer intro than the playing copy).
        if len(tokens) != 2:
            raise CueParseError("@offset takes one argument", line_no)
        seconds = _parse_float(tokens[1], line_no, "offset (seconds)")
        file.offset_ms = int(round(seconds * 1000))
    elif directive == "@default_fx":
        name = tokens[1]
        fx_id = _resolve_fx_by_name(name, line_no, registry)
        fx_cls = registry.get(fx_id)
        positional = tokens[2:]
        params_u8, _params_raw = _build_params_tuple(
            fx_cls, positional, line_no, file=file,
        )
        file.default_fx_id = fx_id
        file.default_fx_params = params_u8
    elif directive == "@artist":
        file.artist = " ".join(tokens[1:])
    elif directive == "@title":
        file.title = " ".join(tokens[1:])
    elif directive == "@ShowSongInfo":
        # Epic 13. Boolean directive, optional explicit value:
        #   @ShowSongInfo            -> True
        #   @ShowSongInfo true|on|1  -> True
        #   @ShowSongInfo false|off|0 -> False
        # The arg-required check in _parse_directive forced len>=2; if
        # the only token is the directive itself the test wouldn't run.
        # Permit a bare directive by treating "no value" as True.
        val = tokens[1].lower() if len(tokens) >= 2 else "true"
        file.show_song_info = val in ("true", "on", "1", "yes")
    elif directive == "@ShowBitmap":
        val = tokens[1].lower() if len(tokens) >= 2 else "true"
        file.show_bitmap = val in ("true", "on", "1", "yes")
    elif directive == "@time_sig":
        # Epic 14 MIR-enrichment metadata. Captured for diagnostics;
        # the orchestrator doesn't act on time_sig at runtime today.
        # Tolerant of garbage: parse as int with fallback to 0.
        try:
            file.time_sig = int(tokens[1])
        except (ValueError, IndexError):
            file.time_sig = 0
    elif directive == "@key":
        # Free-text key name ("A#", "Cm", etc.). Captured as-is.
        file.key = tokens[1] if len(tokens) >= 2 else ""
    elif directive == "@mode":
        # "major" / "minor" / free text. Captured as-is.
        file.mode = tokens[1] if len(tokens) >= 2 else ""
    elif directive == "@duration":
        # Track duration timestamp (MM:SS.cc). Captured as ms for
        # consistency with the rest of CueFile's int-ms convention.
        if len(tokens) >= 2:
            try:
                file.duration_ms = _parse_time(tokens[1], line_no)
            except CueParseError:
                file.duration_ms = 0
    elif directive == "@section":
        # @section <name> <start_ts> <end_ts> [key=value ...]
        # Captured as a list of dicts for future B7 @during automation.
        # Parse defensively - garbled @section lines are skipped, not
        # fatal (so an authoring-tool bug can't poison the cue file).
        if len(tokens) >= 4:
            try:
                name      = tokens[1]
                start_ms  = _parse_time(tokens[2], line_no)
                end_ms    = _parse_time(tokens[3], line_no)
                kvs = {}
                for kv in tokens[4:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        kvs[k] = v
                file.sections.append({
                    "name":     name,
                    "start_ms": start_ms,
                    "end_ms":   end_ms,
                    "extras":   kvs,
                })
            except CueParseError:
                pass
    elif directive == "@analysis_synced":
        file.analysis_synced = tokens[1] if len(tokens) >= 2 else ""
    elif directive == "@analysis_version":
        try:
            file.analysis_version = int(tokens[1])
        except (ValueError, IndexError):
            file.analysis_version = 0
    elif directive == "@analysis_tool":
        file.analysis_tool = tokens[1] if len(tokens) >= 2 else ""
    elif directive == "@during":
        # @during <section_name> <fx_kind> [args...]
        # Stored for post-parse resolution because the @section
        # directive defining the named section may appear later in
        # the file. Resolved in parse_cues() after all directives
        # have been collected.
        if len(tokens) < 3:
            raise CueParseError(
                "@during needs at least a section name and FX name",
                line_no,
            )
        section_name = tokens[1]
        fx_name      = tokens[2]
        # Validate the FX name immediately so authoring-time typos
        # surface on parse. Param parsing also happens now (the
        # parsed FX class is required); the cue object is built at
        # resolution time when we know the section's start_ms.
        fx_id  = _resolve_fx_by_name(fx_name, line_no, registry)
        fx_cls = registry.get(fx_id)
        positional = tokens[3:]
        params_u8, params_raw = _build_params_tuple(
            fx_cls, positional, line_no, file=file,
        )
        file.pending_during.append({
            "section_name": section_name,
            "fx_id":        fx_id,
            "params":       params_u8,
            "params_raw":   params_raw,
            "line_no":      line_no,
        })
    elif directive == "@palette":
        # @palette <name> <hex>[,<hex>...]
        # Stores name -> [(r, g, b), ...] in file.palettes. Multiple
        # @palette directives with the same name overwrite (last
        # wins). Hex values must be #RRGGBB; malformed entries skip
        # silently to avoid breaking the file over a typo.
        if len(tokens) < 3:
            raise CueParseError(
                "@palette needs a name and at least one #RRGGBB colour",
                line_no,
            )
        name = tokens[1]
        # Join all remaining tokens, split on commas; tolerates
        # `@palette stage_d #ff0000,#ff8800` AND
        # `@palette stage_d #ff0000, #ff8800` (whitespace between
        # commas).
        joined = ",".join(tokens[2:])
        colours = []
        for raw in joined.split(","):
            colour = _parse_hex_colour(raw.strip())
            if colour is not None:
                colours.append(colour)
        if colours:
            file.palettes[name] = colours


def _parse_cue(tokens: list, line_no: int, registry, file=None) -> Cue:
    if len(tokens) < 2:
        raise CueParseError("cue needs at least a time and an FX name", line_no)

    time_ms = _parse_time(tokens[0], line_no)
    name = tokens[1]
    rest = tokens[2:]

    # Epic 13: display-content cue lines. Recognised as second-token
    # keywords (HeaderText:, BodyText:, clearscreen). The rest of the
    # line is the text payload (joined with single spaces to recover
    # what the author typed). Empty after the keyword clears that
    # field on the Lume.
    #
    # Escape sequence: literal `\n` (backslash + n, two chars in the
    # cue file) is converted to an actual newline. The Tildagon's
    # text renderer splits on newlines before word-wrap so authors
    # can force line breaks for stylistic effect:
    #
    #     00:03  BodyText: Music of\nthe Spheres
    #
    # renders as two lines. Useful for short multi-line titles +
    # section labels.
    if name == "HeaderText:":
        text = " ".join(rest).replace("\\n", "\n")
        return Cue(time_ms=time_ms, kind="header_text", text=text,
                   line_no=line_no)
    if name == "BodyText:":
        text = " ".join(rest).replace("\\n", "\n")
        return Cue(time_ms=time_ms, kind="body_text", text=text,
                   line_no=line_no)
    if name == "clearscreen":
        if rest:
            raise CueParseError("`clearscreen` takes no arguments", line_no)
        return Cue(time_ms=time_ms, kind="clearscreen", line_no=line_no)

    # `stop` is a parser alias for the Blackout FX (cue_name="blackout").
    # We resolve through the registry so the alias survives any future
    # id reassignment of Blackout.
    if name == "stop":
        if rest:
            raise CueParseError("`stop` takes no arguments", line_no)
        blackout_id = _resolve_fx_by_name("blackout", line_no, registry)
        return Cue(
            time_ms=time_ms, kind="fx", fx_id=blackout_id, line_no=line_no,
        )

    # `bpm N` is a meta cue: changes the file-level default BPM
    # mid-track. Fires no FX itself. FX cues at or after this time
    # pick up the new value; FX cues already running do not (they
    # captured BPM at start).
    if name == "bpm":
        if len(rest) != 1:
            raise CueParseError(
                "`bpm` cue takes one argument (the new BPM)", line_no,
            )
        new_bpm = _parse_int(rest[0], line_no, "BPM")
        return Cue(
            time_ms=time_ms, kind="bpm", bpm=new_bpm, line_no=line_no,
        )

    fx_id = _resolve_fx_by_name(name, line_no, registry)
    fx_cls = registry.get(fx_id)

    # Split rest into positional params and --flag overrides. Flags
    # consume the NEXT token as their value; everything before the
    # first flag (and between flags' value tokens) is positional.
    positional = []
    bpm_override = 0
    buildup_override = 0
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("--"):
            if tok not in _KNOWN_FLAGS:
                raise CueParseError("unknown flag %r" % tok, line_no)
            if i + 1 >= len(rest):
                raise CueParseError("%s needs a value" % tok, line_no)
            value = _parse_int(rest[i + 1], line_no, "%s value" % tok)
            if tok == "--bpm":
                bpm_override = value
            elif tok == "--buildup":
                buildup_override = value
            i += 2
        else:
            positional.append(tok)
            i += 1

    params_u8, params_raw = _build_params_tuple(
        fx_cls, positional, line_no, file=file,
    )
    return Cue(
        time_ms=time_ms, kind="fx", fx_id=fx_id,
        params=params_u8, params_raw=params_raw,
        bpm=bpm_override, buildup_s=buildup_override, line_no=line_no,
    )


def _expand_palette_placeholder(token: str, palettes: dict, line_no: int):
    """Expand a single token. Returns a list - one element if not a
    placeholder, three elements (R G B as strings) if it matches the
    `@name[idx]` pattern.

    Raises CueParseError on unknown palette name or out-of-range
    index. Silent fallback would paint a confusing colour with no
    diagnostic; loud failure surfaces typos at the authoring station
    where they're cheap to fix.
    """
    m = _PALETTE_PLACEHOLDER_RE.match(token)
    if m is None:
        return [token]
    name = m.group(1)
    idx  = int(m.group(2))
    palette = palettes.get(name)
    if palette is None:
        raise CueParseError(
            "unknown palette %r in placeholder %r (known: %s)" % (
                name, token,
                ", ".join(sorted(palettes.keys())) or "(none declared)",
            ),
            line_no,
        )
    if not (0 <= idx < len(palette)):
        raise CueParseError(
            "palette %r index %d out of range (palette has %d colour(s), valid 0..%d)" % (
                name, idx, len(palette),
                max(0, len(palette) - 1),
            ),
            line_no,
        )
    r, g, b = palette[idx]
    return [str(r), str(g), str(b)]


def _expand_palette_placeholders(tokens: list, palettes: dict, line_no: int):
    """Walk a token list, expanding any `@name[idx]` token in place
    into its three RGB sub-tokens. Returns a new list."""
    expanded = []
    for t in tokens:
        expanded.extend(_expand_palette_placeholder(t, palettes, line_no))
    return expanded


def parse_cues(text: str, registry=fx_registry) -> CueFile:
    """Parse cue-file content. Returns a CueFile with cues + lyrics
    sorted by time, with any @offset already applied."""
    file = CueFile()

    # Pass 1: scan @palette directives so `@name[idx]` placeholders
    # used in body cues work regardless of source ordering (a palette
    # declared at the foot of the file is still available to a cue at
    # the top). Cheap: cue files are small and the scan touches only
    # @palette lines.
    lines = list(enumerate(text.splitlines(), start=1))
    for raw_line_no, raw in lines:
        line = _strip_comment(raw).strip()
        if not line.startswith("@palette"):
            continue
        tokens = line.split()
        _parse_directive(tokens, raw_line_no, file, registry)

    # Pass 2: parse the body, expanding palette placeholders inline.
    for raw_line_no, raw in lines:
        # Timestamped lyric comments become Lyric anchors before the
        # comment stripper drops them; ordinary comments still fall
        # through to the empty-line skip below.
        lyric = _maybe_parse_lyric_comment(raw, raw_line_no)
        if lyric is not None:
            file.lyrics.append(lyric)
            continue
        line = _strip_comment(raw).strip()
        if not line:
            continue
        tokens = line.split()
        if tokens[0] == "@palette":
            continue   # already consumed in pass 1
        # Expand `@name[idx]` placeholders to RGB token triplets
        # before directive vs cue dispatch.
        tokens = _expand_palette_placeholders(tokens, file.palettes, raw_line_no)
        if tokens[0].startswith("@"):
            _parse_directive(tokens, raw_line_no, file, registry)
        else:
            file.cues.append(_parse_cue(tokens, raw_line_no, registry, file=file))

    # Resolve `@during <section_name> <fx> ...` directives now that
    # all @section directives have been collected. For each, find
    # the section by name; emit a cue at the section's start_ms.
    # Unknown section names print a warning to stderr (parsing
    # continues - one typo shouldn't kill the rest of the show).
    if file.pending_during:
        sections_by_name = {s["name"]: s for s in file.sections}
        for p in file.pending_during:
            sec = sections_by_name.get(p["section_name"])
            if sec is None:
                import sys
                print(
                    "warning: @during line %d references unknown section "
                    "%r (known: %s)" % (
                        p["line_no"], p["section_name"],
                        ", ".join(sorted(sections_by_name.keys())) or "(none)",
                    ),
                    file=sys.stderr,
                )
                continue
            file.cues.append(Cue(
                kind="fx",
                time_ms=sec["start_ms"],
                fx_id=p["fx_id"],
                params=p["params"],
                params_raw=p["params_raw"],
                line_no=p["line_no"],
            ))
        # Clear pending_during after resolution so the field reflects
        # the post-parse state ("nothing pending").
        file.pending_during = []

    # Apply the file-level offset to every cue and lyric. Doing it
    # post-parse means @offset can sit ANYWHERE in the file (header
    # or footer); cues authored relative to the audio's "0:00" stay
    # readable. The shifted values clamp at 0 so an over-large
    # negative offset doesn't fire cues at negative time (which the
    # scheduler treats as "before the song started").
    if file.offset_ms:
        for c in file.cues:
            c.time_ms = max(0, c.time_ms + file.offset_ms)
        for l in file.lyrics:
            l.time_ms = max(0, l.time_ms + file.offset_ms)

    # Sort by (time, kind-priority) so meta cues fire BEFORE FX cues
    # at the same instant. Lets `bpm 120` followed on the same line
    # by `sparkle_on_beat ...` produce a sparkle that uses 120 BPM,
    # regardless of file order.
    _KIND_ORDER = {"bpm": 0, "fx": 1}
    file.cues.sort(key=lambda c: (c.time_ms, _KIND_ORDER.get(c.kind, 9)))
    file.lyrics.sort(key=lambda l: l.time_ms)
    return file


def parse_cues_file(path, registry=fx_registry) -> CueFile:
    """Read a `.cues` file from disk, parse it, and opportunistically
    load the `<basename>.cues.analysis.json` MIR sidecar if present.

    Sidecar absence is benign: the FX layer falls back to its
    bpm-derived clock when `beats_ms` is empty. So tests + dev
    workflows that don't carry sidecars still work; only files
    enriched via `audio_enrich_cues.py` get the upgraded beat-grid
    runtime sync.
    """
    p = Path(path)
    cue_file = parse_cues(p.read_text(), registry=registry)
    sidecar = p.with_suffix(p.suffix + ".analysis.json")
    if sidecar.exists():
        cue_file.beats_ms = _load_beats_from_sidecar(sidecar)
    return cue_file


def _load_beats_from_sidecar(sidecar_path) -> list:
    """Read the analysis JSON sidecar's `beats` field, convert to
    int-ms (round-half-up), return sorted ascending. Defensive: any
    JSON / type / IO error returns an empty list, matching the
    "no sidecar present" path so a corrupt sidecar can't kill
    playback - only the runtime beat-grid enhancement is lost.
    """
    import json
    try:
        data = json.loads(Path(sidecar_path).read_text())
    except (OSError, ValueError):
        return []
    raw = data.get("beats", [])
    if not isinstance(raw, list):
        return []
    beats_ms = []
    for v in raw:
        try:
            beats_ms.append(int(round(float(v) * 1000.0)))
        except (TypeError, ValueError):
            continue
    beats_ms.sort()
    return beats_ms
