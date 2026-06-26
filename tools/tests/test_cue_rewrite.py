"""Tests for the cue file MIR-enrichment rewriter (Epic 14 B2 + B3)."""

from __future__ import annotations

import pytest

from nocturnation_orchestrator.cue_rewrite import (
    _fmt_ts, _key_palette, _nearest_beat, _parse_ts,
    _pick_section_name, _is_default_name,
    rewrite_cue_file, seed_fx_cues, snap_cue_timestamps,
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


# ---------------------------------------------------------------------------
# Beat snapping (Epic 14 B3)
# ---------------------------------------------------------------------------


class TestNearestBeat:
    def test_target_below_first_beat_returns_first(self):
        assert _nearest_beat([1.0, 2.0, 3.0], 0.0) == 1.0

    def test_target_above_last_beat_returns_last(self):
        assert _nearest_beat([1.0, 2.0, 3.0], 5.0) == 3.0

    def test_exact_match(self):
        assert _nearest_beat([1.0, 2.0, 3.0], 2.0) == 2.0

    def test_closer_to_lower_neighbour(self):
        assert _nearest_beat([1.0, 2.0, 3.0], 2.4) == 2.0

    def test_closer_to_upper_neighbour(self):
        assert _nearest_beat([1.0, 2.0, 3.0], 2.6) == 3.0

    def test_equidistant_picks_lower(self):
        # On a tie, deterministically pick the earlier beat.
        # Matches the "<=" branch in the implementation.
        assert _nearest_beat([1.0, 2.0, 3.0], 2.5) == 2.0


class TestSnapCueTimestamps:
    def test_empty_beats_returns_unchanged(self):
        content = "00:12  pulse\n00:24  stop\n"
        out, stats = snap_cue_timestamps(content, [])
        assert out == content
        assert stats == {
            "snapped": 0, "kept": 0, "non_cue_lines": 0, "max_delta_ms": 0.0,
        }

    def test_cue_within_threshold_snaps(self):
        # Cue at 12.000s; beat at 12.050s (50ms after) -> snap.
        content = "00:12.00  pulse\n"
        out, stats = snap_cue_timestamps(content, [12.05])
        assert "0:12.05  pulse" in out
        assert stats["snapped"] == 1
        assert stats["kept"] == 0

    def test_cue_outside_threshold_kept(self):
        # Cue at 12.000s; nearest beat at 12.500s (500ms away) -> kept.
        content = "00:12.00  pulse\n"
        out, stats = snap_cue_timestamps(content, [12.5])
        # Original timestamp preserved.
        assert "00:12.00  pulse" in out
        assert stats["snapped"] == 0
        assert stats["kept"] == 1

    def test_custom_threshold_honoured(self):
        # 200ms gap, default threshold 150ms = no snap.
        content = "00:12.00  pulse\n"
        out, stats = snap_cue_timestamps(content, [12.2])
        assert stats["snapped"] == 0
        # Same gap, threshold 250ms = snap.
        out2, stats2 = snap_cue_timestamps(content, [12.2], threshold_ms=250)
        assert stats2["snapped"] == 1

    def test_payload_preserved(self):
        # Cue line has full FX args after the timestamp; snap should
        # only touch the timestamp.
        content = "00:35.00  sparkle_on_beat colour=#ff8800 prob=80 group_size=12\n"
        out, _ = snap_cue_timestamps(content, [35.05])
        assert "sparkle_on_beat colour=#ff8800 prob=80 group_size=12" in out
        assert "0:35.05" in out

    def test_directives_and_comments_untouched(self):
        content = (
            "# header comment\n"
            "@artist Coldplay\n"
            "@bpm 178\n"
            "\n"
            "# body comment\n"
            "00:12.00  pulse\n"
        )
        out, stats = snap_cue_timestamps(content, [12.05])
        # Header / comments / blanks pass through unchanged.
        assert "# header comment" in out
        assert "@artist Coldplay" in out
        assert "@bpm 178" in out
        assert "# body comment" in out
        # Cue line snapped.
        assert "0:12.05  pulse" in out
        assert stats["snapped"] == 1
        # The non-cue line count covers comments, directives, and the
        # blank separator line.
        assert stats["non_cue_lines"] == 5

    def test_multiple_cues_mixed(self):
        # 3 cues; 2 snap, 1 outside threshold.
        content = (
            "00:12.00  pulse\n"     # within 50ms of 12.05
            "00:24.00  stop\n"      # within 100ms of 24.10
            "00:42.30  sparkle\n"   # 400ms from nearest beat -> kept
        )
        beats = [12.05, 24.10, 41.90]
        out, stats = snap_cue_timestamps(content, beats)
        assert stats["snapped"] == 2
        assert stats["kept"] == 1
        assert "0:12.05  pulse" in out
        assert "0:24.10  stop" in out
        assert "00:42.30  sparkle" in out   # untouched

    def test_max_delta_tracked(self):
        content = (
            "00:12.00  pulse\n"     # 20ms snap
            "00:24.00  stop\n"      # 100ms snap
        )
        beats = [12.02, 24.10]
        _, stats = snap_cue_timestamps(content, beats)
        assert stats["max_delta_ms"] == pytest.approx(100.0, abs=0.1)

    def test_unsorted_beats_handled(self):
        # Defensive sort - librosa returns sorted but we don't trust callers.
        content = "00:12.00  pulse\n"
        out, stats = snap_cue_timestamps(content, [50.0, 12.05, 30.0])
        assert stats["snapped"] == 1
        assert "0:12.05  pulse" in out

    def test_trailing_newline_preserved(self):
        # Input ends in \n.
        content = "00:12.00  pulse\n"
        out, _ = snap_cue_timestamps(content, [12.05])
        assert out.endswith("\n")
        # Input does NOT end in \n.
        content2 = "00:12.00  pulse"
        out2, _ = snap_cue_timestamps(content2, [12.05])
        assert not out2.endswith("\n")

    def test_idempotent(self):
        # Snap twice with the same beats - second snap is a no-op
        # since the cue is already at the beat.
        content = "00:12.00  pulse\n"
        once, stats1  = snap_cue_timestamps(content, [12.05])
        twice, stats2 = snap_cue_timestamps(once,    [12.05])
        assert once == twice
        # First call snaps (50ms gap); second call's gap is 0 so still
        # "snaps" (0ms <= threshold) but to itself.
        assert stats1["snapped"] == 1
        assert stats2["snapped"] == 1


# ---------------------------------------------------------------------------
# FX seeding (Epic 14 B4)
# ---------------------------------------------------------------------------


class TestKeyPalette:
    def test_c_major_returns_warm_first_colour(self):
        # C major: hue 0 = red. First palette entry should be red-ish
        # (high R, low G/B).
        palette = _key_palette("C", "major")
        r, g, b = palette[0]
        assert r > g
        assert r > b

    def test_c_minor_shifts_to_cool(self):
        # C minor: hue 200 = cyan/blue. First palette entry blue-ish.
        palette = _key_palette("C", "minor")
        r, g, b = palette[0]
        assert b > r

    def test_palette_length(self):
        palette = _key_palette("C", "major")
        assert len(palette) == 4

    def test_all_pitches_produce_valid_palettes(self):
        # Sanity: each of the 12 pitches in each mode returns a
        # 4-tuple palette with RGB values in [30, 200].
        for key in ("C", "C#", "D", "D#", "E", "F", "F#", "G",
                    "G#", "A", "A#", "B"):
            for mode in ("major", "minor"):
                palette = _key_palette(key, mode)
                assert len(palette) == 4
                for r, g, b in palette:
                    assert 30 <= r <= 200
                    assert 30 <= g <= 200
                    assert 30 <= b <= 200

    def test_unknown_key_falls_back_to_c(self):
        # Defensive: an unknown key shouldn't crash; falls back to 0
        # hue (which is C major's hue).
        assert _key_palette("X", "major") == _key_palette("C", "major")


class TestSeedFxCues:
    def test_no_sections_returns_unchanged(self):
        out, stats = seed_fx_cues("@artist X\n", {"sections": []})
        assert out == "@artist X\n"
        assert stats == {"wash_cues": 0, "sparkle_cues": 0, "skipped": 0}

    def test_one_section_per_wash_cue(self):
        analysis = {
            "key": "C", "mode": "major",
            "sections": [
                {"start": 0.0,  "end": 30.0, "loudness_db": -14.0},
                {"start": 30.0, "end": 60.0, "loudness_db": -10.0},
                {"start": 60.0, "end": 90.0, "loudness_db":  -7.0},
            ],
        }
        out, stats = seed_fx_cues("", analysis)
        # Three sections -> three quiet_wash cues.
        assert stats["wash_cues"] == 3
        assert out.count("quiet_wash") == 3
        # All seeded lines carry the # seed tag.
        seed_lines = [l for l in out.splitlines() if "# seed" in l]
        assert len(seed_lines) >= 3

    def test_sparkle_on_above_median_loudness(self):
        # Median of [-14, -10, -7] is -10. Sections at -10 and -7
        # are >= median; both should get sparkle. But the
        # implementation uses strict > so only -7 gets sparkle.
        analysis = {
            "key": "C", "mode": "major",
            "sections": [
                {"start": 0.0,  "end": 30.0, "loudness_db": -14.0},
                {"start": 30.0, "end": 60.0, "loudness_db": -10.0},
                {"start": 60.0, "end": 90.0, "loudness_db":  -7.0},
            ],
        }
        out, stats = seed_fx_cues("", analysis)
        # Strict > median means one sparkle.
        assert stats["sparkle_cues"] == 1
        assert out.count("sparkle_on_beat") == 1

    def test_short_sections_skipped(self):
        analysis = {
            "key": "C", "mode": "major",
            "sections": [
                {"start": 0.0,  "end":  0.3, "loudness_db": -10.0},   # skip
                {"start": 0.3,  "end": 30.0, "loudness_db": -10.0},   # keep
            ],
        }
        out, stats = seed_fx_cues("", analysis)
        assert stats["skipped"] == 1
        assert stats["wash_cues"] == 1

    def test_seed_appends_to_existing_body(self):
        existing = (
            "@artist X\n@title Y\n"
            "\n"
            "00:02   HeaderText: X\n"
            "00:11   pulse 200 100 50\n"
        )
        analysis = {
            "key": "G", "mode": "major",
            "sections": [
                {"start": 0.0, "end": 30.0, "loudness_db": -10.0},
            ],
        }
        out, _ = seed_fx_cues(existing, analysis)
        # Existing body cues are preserved.
        assert "00:02   HeaderText: X" in out
        assert "00:11   pulse 200 100 50" in out
        # New seeded cue appended after existing body.
        assert "# seed" in out
        assert "quiet_wash" in out
        # Confirm ordering: header < existing cues < seed block.
        seed_marker_pos    = out.index("# Seeded FX")
        existing_cue_pos   = out.index("00:11   pulse")
        assert existing_cue_pos < seed_marker_pos

    def test_trailing_newline_preserved(self):
        analysis = {"key": "C", "mode": "major",
                    "sections": [{"start": 0.0, "end": 30.0, "loudness_db": -10.0}]}
        # Input with newline.
        out, _ = seed_fx_cues("@artist X\n", analysis)
        assert out.endswith("\n")
        # Input without.
        out2, _ = seed_fx_cues("@artist X", analysis)
        assert not out2.endswith("\n")

    def test_deterministic_for_same_inputs(self):
        # Same analysis -> identical output. No timestamp / random
        # input to the seeder beyond what's in the analysis.
        analysis = {
            "key": "A#", "mode": "major",
            "sections": [
                {"start": 0.0,  "end": 30.0, "loudness_db": -10.0},
                {"start": 30.0, "end": 60.0, "loudness_db":  -7.0},
            ],
        }
        out_a, _ = seed_fx_cues("", analysis)
        out_b, _ = seed_fx_cues("", analysis)
        assert out_a == out_b

    def test_minor_key_uses_cool_palette(self):
        # Minor key should produce a cooler-toned wash than the same
        # key in major. Compare R-channel of first seed: minor wash
        # should have lower R than major (warm vs cool).
        major_analysis = {
            "key": "C", "mode": "major",
            "sections": [{"start": 0.0, "end": 30.0, "loudness_db": -10.0}],
        }
        minor_analysis = dict(major_analysis, mode="minor")
        major_out, _ = seed_fx_cues("", major_analysis)
        minor_out, _ = seed_fx_cues("", minor_analysis)
        # Extract the R G B values from the first quiet_wash line in each.
        major_rgb = _extract_first_wash_rgb(major_out)
        minor_rgb = _extract_first_wash_rgb(minor_out)
        assert major_rgb[0] > minor_rgb[0]   # major is redder
        assert minor_rgb[2] > major_rgb[2]   # minor is bluer


def _extract_first_wash_rgb(content):
    """Tiny helper: pull (R, G, B) from the first quiet_wash seed line."""
    for line in content.splitlines():
        if "quiet_wash" in line and "# seed" in line:
            parts = line.split()
            # Format: MM:SS.cc   quiet_wash R G B   # seed
            idx = parts.index("quiet_wash")
            return (int(parts[idx + 1]), int(parts[idx + 2]), int(parts[idx + 3]))
    raise AssertionError("no seeded quiet_wash line found in:\n%s" % content)


class TestRewriteSchemaVersion:
    def test_default_version_one(self):
        out = rewrite_cue_file("", _minimal_analysis())
        assert "@analysis_version 1" in out

    def test_custom_version(self):
        out = rewrite_cue_file("", _minimal_analysis(), schema_version=2)
        assert "@analysis_version 2" in out
