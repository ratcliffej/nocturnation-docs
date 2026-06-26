"""Cue file rewrite logic for Epic 14 B2 MIR enrichment.

Takes:
    - an existing cue file's string content (possibly empty)
    - a librosa analysis dict (from `mir.analyse()`)

Returns:
    - a new cue file string with the auto-generated header + sections
      block replaced; hand-edited body cues preserved verbatim.

The "auto-generated" zone is determined by directive identity:
    - `@bpm`, `@time_sig`, `@key`, `@mode`, `@duration`,
      `@analysis_synced`, `@analysis_version`, `@analysis_tool` -
      always replaced (these are the MIR-tool output)
    - `@section` directives - replaced as a block, with author-renamed
      sections preserved by boundary-overlap matching
    - `@artist`, `@title`, `@default_fx`, `@offset`, `@ShowSongInfo`,
      `@ShowBitmap` - preserved (these are author-controlled)
    - Comments + blank lines in the header - preserved (provenance,
      authoring notes) EXCEPT the auto-generated `# WARNING: ...`
      non-Latin warning block emitted by `cues_from_lyrics.py`, which
      gets regenerated.
    - Body cues (any line that starts with a timestamp) - preserved
      verbatim, including author-added `# --- chorus ---` divider
      comments interleaved between them.

The rewrite is idempotent: re-running with the same analysis produces
byte-identical output (modulo `@analysis_synced` timestamp).
"""

from __future__ import annotations

import re


# Directives the MIR tool owns. Listed in the order they're emitted
# in the new header block (so author can rely on a stable layout).
_MIR_DIRECTIVES = (
    "@bpm",
    "@time_sig",
    "@key",
    "@mode",
    "@duration",
    "@analysis_synced",
    "@analysis_version",
    "@analysis_tool",
)

# Directives the author owns; preserved verbatim if present in the
# input. The tool never invents these on the author's behalf.
_AUTHOR_DIRECTIVES = (
    "@artist",
    "@title",
    "@default_fx",
    "@offset",
    "@ShowSongInfo",
    "@ShowBitmap",
)

# Match a cue line - any line that starts with MM:SS or HH:MM:SS
# (optionally with .cc). The PARSER also accepts longer formats; this
# regex is just for boundary detection (header vs body), not full
# validation, so we keep it permissive.
_CUE_LINE_RE = re.compile(r"^\s*\d+:\d+(?::\d+)?(?:\.\d+)?\s")

# Auto-generated warning block emitted by `cues_from_lyrics.py`:
# starts with `# WARNING:` and continues until the next blank line.
_WARNING_FIRST_LINE_RE = re.compile(r"^\s*#\s*WARNING:")


def rewrite_cue_file(content, analysis, *, schema_version=1):
    """Rewrite ``content`` to include MIR enrichment from ``analysis``.

    See module docstring for the merge policy.

    Args:
        content (str): existing cue file content; may be empty (then
            we emit a fresh skeleton). May lack any of the MIR
            directives (first-run case).
        analysis (dict): output of `mir.analyse()`. Must contain at
            minimum tempo, key, mode, duration_s, sections,
            analysis_tool, synced.
        schema_version (int): value to stamp into `@analysis_version`.
            Default 1 (current schema). Future schema migrations bump.

    Returns:
        str: the rewritten cue file content, ending with a trailing
        newline.
    """
    lines = content.splitlines() if content else []
    header_zone, body_zone = _split_header_body(lines)

    # Surface the author's pre-existing @section directives so we can
    # preserve renames across re-runs.
    existing_sections = _parse_section_directives(header_zone)

    # Build the new header line by line.
    new_header = _build_new_header(
        header_zone, analysis, existing_sections, schema_version=schema_version,
    )

    return "\n".join(new_header + body_zone) + "\n"


def _split_header_body(lines):
    """Partition input lines into (header, body) at the first cue line.

    A "cue line" is anything starting with a timestamp. Lines before
    that go to the header; lines from there to EOF go to the body
    (verbatim). Trailing blank lines on the header are stripped from
    the header list and re-inserted by the builder.
    """
    for i, line in enumerate(lines):
        if _CUE_LINE_RE.match(line):
            return lines[:i], lines[i:]
    # No cue lines at all - everything is header.
    return lines[:], []


def _parse_section_directives(header_lines):
    """Return list of (name, start, end) tuples for existing @section.

    Used to preserve author renames across re-runs by matching new
    sections to old ones via boundary-overlap.
    """
    out = []
    for line in header_lines:
        stripped = line.strip()
        if not stripped.startswith("@section"):
            continue
        tokens = stripped.split()
        # Format: @section <name> <start_ts> <end_ts> [...]
        if len(tokens) < 4:
            continue
        name = tokens[1]
        try:
            start = _parse_ts(tokens[2])
            end = _parse_ts(tokens[3])
        except ValueError:
            continue
        out.append((name, start, end))
    return out


def _parse_ts(token):
    """Parse `MM:SS` or `MM:SS.cc` (or `HH:MM:SS[.cc]`) into seconds."""
    parts = token.split(":")
    if len(parts) == 2:
        mm, ss = parts
        seconds = int(mm) * 60 + float(ss)
    elif len(parts) == 3:
        hh, mm, ss = parts
        seconds = int(hh) * 3600 + int(mm) * 60 + float(ss)
    else:
        raise ValueError("bad timestamp %r" % token)
    return seconds


def _fmt_ts(seconds):
    """Format seconds as `MM:SS.cc` (cue-file-canonical form)."""
    total = int(seconds)
    cs = int(round((seconds - total) * 100))
    if cs >= 100:
        total += 1
        cs = 0
    minutes = total // 60
    secs = total % 60
    return "%d:%02d.%02d" % (minutes, secs, cs)


def _build_new_header(old_header, analysis, existing_sections, *, schema_version):
    """Reassemble the header block with refreshed MIR directives.

    Walks the old header keeping author-owned content, drops MIR-owned
    content (which will be re-emitted), drops the auto-warning block
    (regenerated by `cues_from_lyrics.py` not us; if it was there we
    just don't carry it forward), then appends fresh MIR + section
    directives in a stable order.
    """
    out = []
    skip_warning = False
    saw_blank_after_directives = False

    for line in old_header:
        stripped = line.strip()

        # Warning block: skip from "# WARNING:" until the next blank.
        if skip_warning:
            if stripped == "":
                skip_warning = False
                # Drop the blank too - new header has its own spacing.
                continue
            continue
        if _WARNING_FIRST_LINE_RE.match(line):
            skip_warning = True
            continue

        if stripped.startswith("@"):
            tokens = stripped.split(None, 1)
            directive = tokens[0]
            if directive in _MIR_DIRECTIVES or directive == "@section":
                # Drop - re-emitted below.
                continue
            # Author directive - keep.
            out.append(line)
            continue

        # Plain comment / blank: keep. The "@artist ... @title ..."
        # blocks typically have a blank line after them; preserve.
        out.append(line)

    # Strip trailing blank lines from `out` so we can append the new
    # MIR block cleanly. We'll add our own blank separator.
    while out and out[-1].strip() == "":
        out.pop()

    # MIR-emitted block.
    #
    # Deliberately NO comment header here. An earlier draft emitted
    # "# MIR enrichment (re-run ...)" comments which then got
    # preserved by re-runs (the rewriter can't tell them from
    # author comments), causing the block to accumulate on each
    # re-enrichment. The @analysis_synced + @analysis_tool
    # directives are sufficient self-documentation; the
    # cue-file-schema.md doc carries the "how to re-run" info.
    out.append("")
    out.append("@bpm        %d"       % int(round(analysis["tempo"])))
    out.append("@time_sig   %d"       % analysis.get("time_sig", 4))
    out.append("@key        %s"       % analysis["key"])
    out.append("@mode       %s"       % analysis["mode"])
    out.append("@duration   %s"       % _fmt_ts(analysis["duration_s"]))
    out.append("@analysis_synced  %s" % analysis["synced"])
    out.append("@analysis_version %d" % schema_version)
    out.append("@analysis_tool    %s" % analysis.get("analysis_tool", "librosa"))

    sections = analysis.get("sections", [])
    if sections:
        out.append("")
        for i, sec in enumerate(sections):
            name = _pick_section_name(sec, i, existing_sections)
            tempo = sec.get("tempo")
            loud  = sec.get("loudness_db")
            extras = []
            if tempo is not None:
                extras.append("tempo=%.1f" % tempo)
            if loud is not None:
                extras.append("loudness=%.1f" % loud)
            extras_str = (" " + " ".join(extras)) if extras else ""
            out.append("@section %-10s %s  %s%s" % (
                name, _fmt_ts(sec["start"]), _fmt_ts(sec["end"]), extras_str,
            ))

    out.append("")   # blank line between header and body
    return out


def _pick_section_name(new_section, index, existing_sections):
    """Pick a name for a new section, preserving author renames where
    boundaries match.

    Match rule: midpoint of the new section falls within an existing
    section's boundaries, AND the existing name is non-default (i.e.
    not `section1`, `section2`, ...). If matched, reuse the existing
    name; otherwise emit a fresh `sectionN`.
    """
    mid = (new_section["start"] + new_section["end"]) / 2.0
    for name, old_start, old_end in existing_sections:
        if old_start <= mid <= old_end and not _is_default_name(name):
            return name
    return "section%d" % (index + 1)


_DEFAULT_NAME_RE = re.compile(r"^section\d+$")


def _is_default_name(name):
    """True if ``name`` looks like the tool's auto-emitted default."""
    return bool(_DEFAULT_NAME_RE.match(name))


# ---------------------------------------------------------------------------
# Beat snapping (Epic 14 B3, --snap flag)
# ---------------------------------------------------------------------------

# Match a leading timestamp on a cue line so we can replace it with
# the snapped value. Captures the timestamp token; the rest of the
# line (kind + args) is left alone.
_CUE_LINE_TS_RE = re.compile(
    r"^(\s*)(\d+:\d+(?::\d+)?(?:\.\d+)?)(\s)"
)


def snap_cue_timestamps(content, beats, *, threshold_ms=150):
    """Snap cue-line timestamps to the nearest detected beat.

    Walks ``content`` line-by-line. For each line that starts with a
    timestamp (i.e. a cue line, not a directive or comment), finds
    the nearest beat in ``beats``. If the gap is within
    ``threshold_ms``, replaces the timestamp with the snapped beat
    time (formatted MM:SS.cc per the cue file schema). Otherwise
    leaves the line alone.

    Cues outside the threshold are left at their authored time -
    this is deliberate. A cue at 00:42.300 with the nearest beat at
    00:41.000 (gap 1300 ms) probably wasn't meant for a beat at
    all (think: a structural cue at the start of a wash, or a
    BodyText line that lands mid-phrase by design). Yanking it
    onto a beat would distort the author's intent.

    Args:
        content (str): cue file content.
        beats (list[float]): beat positions in seconds, monotonic
            increasing. Empty list = no-op.
        threshold_ms (int): max gap to snap, in milliseconds.
            Default 150 (half a beat at 200 BPM); operator overrides
            via the CLI ``--snap-threshold-ms`` flag.

    Returns:
        (new_content, stats) tuple where stats is a dict:

            {
              "snapped":      int (cues moved to a beat),
              "kept":         int (cues outside threshold; left alone),
              "non_cue_lines": int (header / comment / blank lines),
              "max_delta_ms": float (largest snap that fired),
            }

        Stats are useful for the CLI's user-facing summary.
    """
    if not beats:
        return content, {
            "snapped": 0, "kept": 0, "non_cue_lines": 0, "max_delta_ms": 0.0,
        }
    # Defensive sort; librosa returns sorted but we don't trust callers.
    sorted_beats = sorted(beats)

    out_lines = []
    snapped = 0
    kept = 0
    non_cue = 0
    max_delta = 0.0

    # Preserve trailing newline behaviour: if content ends with "\n",
    # splitlines() drops it and we re-add at the end.
    trailing_newline = content.endswith("\n")
    for line in content.splitlines():
        match = _CUE_LINE_TS_RE.match(line)
        if not match:
            out_lines.append(line)
            non_cue += 1
            continue
        leading_ws, ts_str, sep = match.group(1), match.group(2), match.group(3)
        try:
            cue_seconds = _parse_ts(ts_str)
        except ValueError:
            out_lines.append(line)
            non_cue += 1
            continue
        nearest = _nearest_beat(sorted_beats, cue_seconds)
        delta_ms = abs(cue_seconds - nearest) * 1000.0
        if delta_ms <= threshold_ms:
            new_ts = _fmt_ts(nearest)
            new_line = (
                leading_ws + new_ts + sep + line[match.end():]
            )
            out_lines.append(new_line)
            snapped += 1
            max_delta = max(max_delta, delta_ms)
        else:
            out_lines.append(line)
            kept += 1

    result = "\n".join(out_lines)
    if trailing_newline:
        result += "\n"
    return result, {
        "snapped":       snapped,
        "kept":          kept,
        "non_cue_lines": non_cue,
        "max_delta_ms":  max_delta,
    }


def _nearest_beat(sorted_beats, target_s):
    """Binary search for the beat closest to ``target_s`` in seconds.

    Returns the beat time. Empty list raises - caller guards.
    """
    # Manual binary search; bisect.bisect_left is in stdlib but we
    # save the import.
    lo, hi = 0, len(sorted_beats) - 1
    if target_s <= sorted_beats[0]:
        return sorted_beats[0]
    if target_s >= sorted_beats[hi]:
        return sorted_beats[hi]
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if sorted_beats[mid] < target_s:
            lo = mid
        else:
            hi = mid
    # lo and hi now flank target_s; pick whichever is closer.
    if target_s - sorted_beats[lo] <= sorted_beats[hi] - target_s:
        return sorted_beats[lo]
    return sorted_beats[hi]
