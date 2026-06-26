"""Tests for the cue file MIR-enrichment rewriter (Epic 14 B2)."""

from __future__ import annotations

import pytest

from nocturnation_orchestrator.cue_rewrite import (
    _fmt_ts, _parse_ts, _pick_section_name, _is_default_name,
    rewrite_cue_file,
)


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


class TestParseTs:
    def test_minutes_seconds(self):
        assert _parse_ts("01:30") == 90.0

    def test_minutes_seconds_centiseconds(self):
        assert _parse_ts("01:30.50") == 90.5
        assert _parse_ts("00:00.12") == pytest.approx(0.12)

    def test_hours_minutes_seconds(self):
        assert _parse_ts("01:02:30") == 3750.0

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_ts("not-a-timestamp")
        with pytest.raises(ValueError):
            _parse_ts("01")


class TestFmtTs:
    def test_round_trip(self):
        for seconds in (0.0, 0.5, 12.34, 90.0, 90.50, 211.5):
            assert _parse_ts(_fmt_ts(seconds)) == pytest.approx(seconds, abs=0.01)

    def test_centiseconds_pad(self):
        assert _fmt_ts(30.05) == "0:30.05"
        assert _fmt_ts(30.5)  == "0:30.50"

    def test_zero(self):
        assert _fmt_ts(0.0) == "0:00.00"


# ---------------------------------------------------------------------------
# Section name preservation (rename-across-resync)
# ---------------------------------------------------------------------------


class TestIsDefaultName:
    def test_default_names(self):
        assert _is_default_name("section1")
        assert _is_default_name("section42")

    def test_renamed(self):
        assert not _is_default_name("verse1")
        assert not _is_default_name("chorus2")
        assert not _is_default_name("bridge")
        assert not _is_default_name("intro")


class TestPickSectionName:
    def test_no_existing_uses_default(self):
        new = {"start": 10.0, "end": 30.0}
        assert _pick_section_name(new, 0, []) == "section1"
        assert _pick_section_name(new, 4, []) == "section5"

    def test_renamed_existing_preserved_on_match(self):
        # Existing author rename "chorus1" at 0-30; new section midpoint
        # 20 falls within - reuse the name.
        existing = [("chorus1", 0.0, 30.0)]
        new = {"start": 10.0, "end": 30.0}
        assert _pick_section_name(new, 2, existing) == "chorus1"

    def test_default_existing_not_preserved(self):
        # Existing "section3" is the auto-default, not author work;
        # always re-default. Otherwise re-runs would freeze the names
        # against analysis drift.
        existing = [("section3", 0.0, 30.0)]
        new = {"start": 10.0, "end": 30.0}
        assert _pick_section_name(new, 0, existing) == "section1"

    def test_no_overlap_falls_back(self):
        existing = [("chorus1", 60.0, 90.0)]
        new = {"start": 10.0, "end": 30.0}
        assert _pick_section_name(new, 0, existing) == "section1"


# ---------------------------------------------------------------------------
# rewrite_cue_file - end-to-end on minimal analyses
# ---------------------------------------------------------------------------


def _minimal_analysis(**overrides):
    """Build a small analysis dict for rewrite tests. The rewrite only
    looks at scalar fields + sections; beats / onsets / chroma are
    sidecar-only and irrelevant here."""
    base = {
        "tempo":          178.0,
        "time_sig":       4,
        "key":            "A#",
        "mode":           "major",
        "duration_s":     211.5,
        "sections": [
            {"start":   0.0, "end":  11.5, "tempo": 178.0, "loudness_db": -14.2},
            {"start":  11.5, "end":  35.0, "tempo": 178.0, "loudness_db": -10.7},
            {"start":  35.0, "end":  55.7, "tempo": 178.0, "loudness_db":  -7.3},
        ],
        "analysis_tool":  "librosa",
        "synced":         "2026-06-26T18:14:00Z",
    }
    base.update(overrides)
    return base


class TestRewriteEmptyInput:
    def test_empty_string_emits_full_header(self):
        out = rewrite_cue_file("", _minimal_analysis())
        assert "@bpm        178"             in out
        assert "@time_sig   4"               in out
        assert "@key        A#"              in out
        assert "@mode       major"           in out
        assert "@analysis_tool    librosa"   in out
        assert "@section section1"           in out

    def test_centisecond_section_boundaries(self):
        out = rewrite_cue_file("", _minimal_analysis())
        # Sections render with MM:SS.cc precision (Epic 14 schema).
        assert "0:00.00"  in out
        assert "0:11.50"  in out


class TestRewritePreservesAuthorDirectives:
    def test_artist_title_default_fx_preserved(self):
        existing = (
            "@artist     Coldplay\n"
            "@title      Higher Power\n"
            "@default_fx quiet_wash 20 40 80\n"
            "@offset     0.085\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        assert "@artist     Coldplay"          in out
        assert "@title      Higher Power"      in out
        assert "@default_fx quiet_wash 20 40 80" in out
        assert "@offset     0.085"             in out

    def test_show_song_info_preserved(self):
        existing = "@artist X\n@title Y\n@ShowSongInfo\n"
        out = rewrite_cue_file(existing, _minimal_analysis())
        assert "@ShowSongInfo" in out

    def test_comment_provenance_preserved(self):
        existing = (
            "# coldplay-higher-power.cues\n"
            "# Authored 2026-06-26.\n"
            "#\n"
            "@artist Coldplay\n"
            "@title  Higher Power\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        assert "# coldplay-higher-power.cues" in out
        assert "# Authored 2026-06-26." in out


class TestRewriteReplacesMirDirectives:
    def test_old_bpm_replaced(self):
        existing = "@artist X\n@title Y\n@bpm 120\n"
        out = rewrite_cue_file(existing, _minimal_analysis())
        # Old @bpm 120 dropped.
        assert "@bpm 120" not in out
        # New @bpm 178 emitted.
        assert "@bpm        178" in out

    def test_old_default_sections_dropped(self):
        # Default-named (sectionN) old directives are dropped on
        # re-enrichment - they're "auto-generated" not author work, so
        # rename-preservation doesn't apply. Boundaries from the new
        # analysis are emitted afresh.
        existing = (
            "@artist X\n"
            "@title Y\n"
            "@section section1 0:00 0:10\n"
            "@section section2 0:10 0:20\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        # New @section directives emerge with the new analysis's
        # boundaries (0:00-0:11.50, 0:11.50-0:35, 0:35-0:55.70).
        assert "@section section1" in out
        assert "@section section3" in out
        # Old boundary 0:10 0:20 no longer present.
        assert "0:20.00" not in out


class TestRewriteSectionRenamePreservation:
    def test_renamed_sections_carry_across_resync(self):
        # Author has renamed section2 to "verse1". New analysis returns
        # the same boundaries; the rename should survive.
        existing = (
            "@artist X\n@title Y\n"
            "@section section1   0:00.00  0:11.50\n"
            "@section verse1     0:11.50  0:35.00\n"
            "@section section3   0:35.00  0:55.70\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        # The middle section keeps "verse1"; the auto-default names
        # for the others get re-emitted.
        assert "@section section1" in out
        assert "@section verse1"   in out
        assert "@section section3" in out

    def test_renamed_section_with_shifted_boundary_still_matched(self):
        # Analysis re-run shifts section midpoint slightly. As long as
        # the new midpoint falls within the old range, the rename
        # survives.
        existing = (
            "@artist X\n@title Y\n"
            "@section verse1  0:10.00  0:36.00\n"   # midpoint at 23s
        )
        # New analysis returns a section 11.5-35.0 (midpoint 23.25).
        out = rewrite_cue_file(existing, _minimal_analysis())
        # The second section (11.5-35.0) has midpoint 23.25, which
        # falls within the existing 10-36 range.
        assert "@section verse1" in out


class TestRewriteBodyPreservation:
    def test_body_cues_preserved_verbatim(self):
        existing = (
            "@artist X\n@title Y\n"
            "\n"
            "00:02  HeaderText: Coldplay\n"
            "00:11  sparkle_on_beat colour=#ff8800 prob=80\n"
            "01:05  stop\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        assert "00:02  HeaderText: Coldplay" in out
        assert "00:11  sparkle_on_beat colour=#ff8800 prob=80" in out
        assert "01:05  stop" in out

    def test_section_comments_in_body_preserved(self):
        existing = (
            "@artist X\n@title Y\n"
            "\n"
            "# --- chorus ---\n"
            "00:35  sparkle_on_beat prob=85\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        assert "# --- chorus ---" in out
        assert "00:35  sparkle_on_beat prob=85" in out


class TestRewriteWarningStripping:
    def test_lyric_emitter_warning_block_dropped(self):
        # cues_from_lyrics.py emits a "# WARNING:" block when the
        # lyrics have non-Latin scripts. On re-enrichment we drop it
        # (the warning is re-emitted by the lyric tool if you re-run
        # IT; the MIR tool doesn't own that warning).
        existing = (
            "@artist X\n@title Y\n"
            "#\n"
            "# WARNING: lyrics contain non-Latin script(s): Hangul.\n"
            "# These will render as missing-glyph boxes on the Tildagon.\n"
            "\n"
            "@bpm 120\n"
        )
        out = rewrite_cue_file(existing, _minimal_analysis())
        assert "WARNING" not in out


class TestRewriteIdempotency:
    def test_two_rewrites_produce_same_output(self):
        # Idempotency: running enrich twice with the same analysis
        # gives byte-identical output (the timestamp is in the
        # analysis dict so it's stable, not regenerated per call).
        existing = (
            "@artist Coldplay\n"
            "@title  Higher Power\n"
            "@default_fx quiet_wash 20 40 80\n"
            "\n"
            "00:02  HeaderText: Coldplay\n"
            "00:11  sparkle_on_beat prob=80\n"
        )
        analysis = _minimal_analysis()
        first  = rewrite_cue_file(existing, analysis)
        second = rewrite_cue_file(first,    analysis)
        assert first == second


class TestRewriteSchemaVersion:
    def test_default_version_one(self):
        out = rewrite_cue_file("", _minimal_analysis())
        assert "@analysis_version 1" in out

    def test_custom_version(self):
        out = rewrite_cue_file("", _minimal_analysis(), schema_version=2)
        assert "@analysis_version 2" in out
