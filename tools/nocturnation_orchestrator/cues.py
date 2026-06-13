"""Cue-file parser.

Reads `.cues` files (one cue per line, whitespace separated) and emits
a `CueFile` ready for the orchestrator main loop to walk.

Grammar (informal):

    file        := (directive | cue | blank | comment)*
    directive   := '@' name token*
    cue         := time fx_token positional* flag*
    fx_token    := cue_name | 'stop'
    positional  := integer
    flag        := '--' name integer
    time        := MM:SS | M:SS | H:MM:SS | HH:MM:SS
    comment     := '#' .* end-of-line  (allowed anywhere; truncates the line)

Recognised directives:

    @bpm        N               file-default BPM
    @default_fx name [params]   FX that runs before the first cue / after `stop`
    @artist     free text       label used by the matcher
    @title      free text       label used by the matcher

Recognised flags:

    --bpm       N               per-cue BPM override (FX-engine knob)
    --buildup   N               per-cue buildup_s override

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

    Attributes:
        time_ms (int): absolute time into the track, in milliseconds.
        fx_id (int): registry id; 0 = `stop` (cancel current FX).
        params (tuple[int, ...]): six u8s, ready for FX.start()'s
            positional consumption. Reserved slots are 0.
        bpm (int): per-cue BPM override; 0 = inherit file/default.
        buildup_s (int): per-cue buildup window; 0 = none.
        line_no (int): source line for diagnostics.
    """
    time_ms: int
    fx_id: int
    params: tuple = (0, 0, 0, 0, 0, 0)
    bpm: int = 0
    buildup_s: int = 0
    line_no: int = 0


@dataclass
class CueFile:
    """Parsed cue file.

    cues is sorted by time_ms ascending. default_fx_* is the FX (if any)
    the orchestrator runs before the first cue and after a `stop`.
    """
    artist: str = ""
    title: str = ""
    default_bpm: int = 0
    default_fx_id: int = 0
    default_fx_params: tuple = (0, 0, 0, 0, 0, 0)
    cues: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^(?:(\d+):)?(\d+):(\d{2})$")
_INT_RE = re.compile(r"^-?\d+$")


def _parse_time(token: str, line_no: int) -> int:
    """Parse MM:SS / M:SS / H:MM:SS to milliseconds."""
    m = _TIME_RE.match(token)
    if not m:
        raise CueParseError("invalid time %r (expected MM:SS or H:MM:SS)" % token, line_no)
    h = int(m.group(1) or 0)
    minutes = int(m.group(2))
    secs = int(m.group(3))
    if secs >= 60:
        raise CueParseError("seconds field >= 60 in %r" % token, line_no)
    return ((h * 60 + minutes) * 60 + secs) * 1000


def _parse_int(token: str, line_no: int, what: str = "integer") -> int:
    if not _INT_RE.match(token):
        raise CueParseError("expected %s, got %r" % (what, token), line_no)
    return int(token)


def _strip_comment(line: str) -> str:
    """Drop everything from the first '#' onwards."""
    i = line.find("#")
    return line if i < 0 else line[:i]


def _resolve_fx_by_name(name: str, line_no: int, registry) -> int:
    """Map a cue_name to its fx_id. Raises CueParseError on miss."""
    for fx_id in registry.all_ids():
        cls = registry.get(fx_id)
        if cls.cue_name == name:
            return fx_id
    raise CueParseError("unknown FX %r" % name, line_no)


def _build_params_tuple(fx_cls, positional: list, line_no: int) -> tuple:
    """Map a list of positional cue-file values to the FX's six-u8 tuple.

    Reserved slots in PARAMS are skipped during positional assignment
    (they stay 0 in the output). The caller's positional list must not
    exceed the count of non-reserved slots.
    """
    out = [0, 0, 0, 0, 0, 0]
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
    for value_token, (slot, unit) in zip(positional, named_slots):
        value = _parse_int(value_token, line_no, "param value")
        try:
            out[slot] = convert_to_u8(value, unit)
        except ValueError as exc:
            raise CueParseError(str(exc), line_no) from None
    return tuple(out)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_KNOWN_FLAGS = ("--bpm", "--buildup")
_KNOWN_DIRECTIVES = ("@bpm", "@default_fx", "@artist", "@title")


def _parse_directive(tokens: list, line_no: int, file: CueFile, registry) -> None:
    directive = tokens[0]
    if directive not in _KNOWN_DIRECTIVES:
        raise CueParseError("unknown directive %r" % directive, line_no)
    if len(tokens) < 2:
        raise CueParseError("%s needs an argument" % directive, line_no)

    if directive == "@bpm":
        if len(tokens) != 2:
            raise CueParseError("@bpm takes one argument", line_no)
        file.default_bpm = _parse_int(tokens[1], line_no, "BPM")
    elif directive == "@default_fx":
        name = tokens[1]
        fx_id = _resolve_fx_by_name(name, line_no, registry)
        fx_cls = registry.get(fx_id)
        positional = tokens[2:]
        params = _build_params_tuple(fx_cls, positional, line_no)
        file.default_fx_id = fx_id
        file.default_fx_params = params
    elif directive == "@artist":
        file.artist = " ".join(tokens[1:])
    elif directive == "@title":
        file.title = " ".join(tokens[1:])


def _parse_cue(tokens: list, line_no: int, registry) -> Cue:
    if len(tokens) < 2:
        raise CueParseError("cue needs at least a time and an FX name", line_no)

    time_ms = _parse_time(tokens[0], line_no)
    name = tokens[1]
    rest = tokens[2:]

    # `stop` is the cancel sentinel; takes no params or flags.
    if name == "stop":
        if rest:
            raise CueParseError("`stop` takes no arguments", line_no)
        return Cue(time_ms=time_ms, fx_id=0, line_no=line_no)

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

    params = _build_params_tuple(fx_cls, positional, line_no)
    return Cue(
        time_ms=time_ms, fx_id=fx_id, params=params,
        bpm=bpm_override, buildup_s=buildup_override, line_no=line_no,
    )


def parse_cues(text: str, registry=fx_registry) -> CueFile:
    """Parse cue-file content. Returns a CueFile with cues sorted by time."""
    file = CueFile()
    for raw_line_no, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        tokens = line.split()
        if tokens[0].startswith("@"):
            _parse_directive(tokens, raw_line_no, file, registry)
        else:
            file.cues.append(_parse_cue(tokens, raw_line_no, registry))
    file.cues.sort(key=lambda c: c.time_ms)
    return file


def parse_cues_file(path, registry=fx_registry) -> CueFile:
    """Read a `.cues` file from disk and parse."""
    return parse_cues(Path(path).read_text(), registry=registry)
