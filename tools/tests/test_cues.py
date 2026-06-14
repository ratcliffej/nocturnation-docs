"""Cue-file parser tests (Epic 10 B7)."""

import pytest

from nocturnation_orchestrator.cues import (
    Cue, CueFile, CueParseError, Lyric, parse_cues,
)
from nocturnation_orchestrator.fx import library  # noqa: F401  side-effects
from nocturnation_orchestrator.fx.registry import fx_registry


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------

class TestTimeParsing:
    def test_mm_ss(self):
        f = parse_cues("00:30 stop")
        assert f.cues[0].time_ms == 30_000

    def test_m_ss(self):
        f = parse_cues("1:05 stop")
        assert f.cues[0].time_ms == 65_000

    def test_h_mm_ss(self):
        f = parse_cues("1:02:30 stop")
        assert f.cues[0].time_ms == (1 * 3600 + 2 * 60 + 30) * 1000

    def test_seconds_field_must_be_under_60(self):
        with pytest.raises(CueParseError):
            parse_cues("00:99 stop")

    def test_bad_time_format(self):
        with pytest.raises(CueParseError):
            parse_cues("30 stop")
        with pytest.raises(CueParseError):
            parse_cues("0:30:30:30 stop")

    def test_tenths_of_a_second(self):
        # MM:SS.x  -> ms
        f = parse_cues("00:30.5 stop")
        assert f.cues[0].time_ms == 30_500

    def test_centiseconds(self):
        # Match LRC grain so LD can paste timestamps in directly.
        f = parse_cues("00:30.50 stop")
        assert f.cues[0].time_ms == 30_500
        f2 = parse_cues("00:30.05 stop")
        assert f2.cues[0].time_ms == 30_050

    def test_milliseconds(self):
        f = parse_cues("00:30.123 stop")
        assert f.cues[0].time_ms == 30_123

    def test_fractional_with_hours(self):
        f = parse_cues("1:02:30.5 stop")
        assert f.cues[0].time_ms == (1 * 3600 + 2 * 60 + 30) * 1000 + 500

    def test_fractional_rejects_more_than_3_digits(self):
        with pytest.raises(CueParseError):
            parse_cues("00:30.1234 stop")


# ---------------------------------------------------------------------------
# Lexical: comments, whitespace, blanks
# ---------------------------------------------------------------------------

class TestLexer:
    def test_blank_lines_skipped(self):
        f = parse_cues("\n\n\n00:30 stop\n\n")
        assert len(f.cues) == 1

    def test_comment_only_line_skipped(self):
        f = parse_cues("# just a comment\n00:30 stop")
        assert len(f.cues) == 1

    def test_trailing_comment_on_cue(self):
        f = parse_cues("00:30 stop  # outro start\n")
        assert len(f.cues) == 1

    def test_multiple_whitespace_between_fields(self):
        f = parse_cues("00:30   sparkle_on_beat    255   0   255  100")
        assert f.cues[0].params == (255, 0, 255, 255)

    def test_tab_separated(self):
        f = parse_cues("00:30\tsparkle_on_beat\t255\t0\t255\t100")
        assert f.cues[0].params == (255, 0, 255, 255)

    def test_lyric_style_comments_between_rows(self):
        text = """
            00:00 quiet_wash 20 40 80

            # --- Intro ---
            # "When you try your best..."
            00:30 sparkle_on_beat 80 200 200 100

            # --- Build ---
            01:20 linear_buildup 255 0 0 100 64 --buildup 8
        """
        f = parse_cues(text)
        assert len(f.cues) == 3
        assert f.cues[1].time_ms == 30_000
        assert f.cues[2].buildup_s == 8


# ---------------------------------------------------------------------------
# Directives
# ---------------------------------------------------------------------------

class TestDirectives:
    def test_bpm(self):
        f = parse_cues("@bpm 138")
        assert f.default_bpm == 138

    def test_artist_title_free_text(self):
        f = parse_cues("@artist The Cure\n@title Pictures Of You")
        assert f.artist == "The Cure"
        assert f.title == "Pictures Of You"

    def test_default_fx_resolves_name(self):
        f = parse_cues("@default_fx quiet_wash 20 40 80")
        qw = fx_registry.get(1)
        assert f.default_fx_id == qw.id
        # quiet_wash declares 5 slots; params tuple is sized to match.
        assert f.default_fx_params == (20, 40, 80, 0, 0)

    def test_default_fx_no_params(self):
        f = parse_cues("@default_fx quiet_wash")
        assert f.default_fx_id == 1
        assert f.default_fx_params == (0, 0, 0, 0, 0)

    def test_unknown_directive(self):
        with pytest.raises(CueParseError) as exc:
            parse_cues("@whatever 5")
        assert "unknown directive" in str(exc.value)

    def test_directive_needs_argument(self):
        with pytest.raises(CueParseError):
            parse_cues("@bpm")


class TestOffsetDirective:
    """@offset shifts every cue + lyric by a fixed amount, at parse
    time. Used to compensate for album-mastering silence padding so a
    .cues file authored against one release stays in sync when the
    same song is played from a different release."""

    def test_positive_offset_delays_cues(self):
        text = """
            @offset 1.5
            00:30 sparkle_on_beat 100 100 100 100
            00:45 sparkle_on_beat 255 0 0 100
        """
        f = parse_cues(text)
        assert f.offset_ms == 1500
        assert f.cues[0].time_ms == 31_500
        assert f.cues[1].time_ms == 46_500

    def test_negative_offset_advances_cues(self):
        text = """
            @offset -2
            00:30 sparkle_on_beat 100 100 100 100
        """
        f = parse_cues(text)
        assert f.offset_ms == -2000
        assert f.cues[0].time_ms == 28_000

    def test_offset_applies_to_lyrics_too(self):
        text = """
            @offset 1.2
            # 00:30  When you try your best
            00:30 sparkle_on_beat 100 100 100 100
        """
        f = parse_cues(text)
        assert f.lyrics[0].time_ms == 31_200
        assert f.cues[0].time_ms == 31_200

    def test_offset_clamps_at_zero(self):
        # Over-large negative offset must not push cues to negative
        # times; the scheduler treats negatives as "before song
        # started" and they'd fire spuriously on the first advance.
        text = """
            @offset -60
            00:05 sparkle_on_beat 100 100 100 100
        """
        f = parse_cues(text)
        assert f.cues[0].time_ms == 0

    def test_offset_can_sit_anywhere_in_file(self):
        # Directive precedence is by parse value at end-of-parse, not
        # position-in-file, so a trailing @offset works the same as a
        # leading one.
        leading = parse_cues("@offset 1.0\n00:30 stop\n")
        trailing = parse_cues("00:30 stop\n@offset 1.0\n")
        assert leading.cues[0].time_ms == trailing.cues[0].time_ms

    def test_fractional_offset_centisecond_grain(self):
        text = """
            @offset 0.25
            00:30 stop
        """
        f = parse_cues(text)
        assert f.offset_ms == 250
        assert f.cues[0].time_ms == 30_250

    def test_no_offset_means_zero(self):
        f = parse_cues("00:30 stop\n")
        assert f.offset_ms == 0

    def test_offset_needs_argument(self):
        with pytest.raises(CueParseError):
            parse_cues("@offset")

    def test_offset_rejects_non_numeric(self):
        with pytest.raises(CueParseError) as exc:
            parse_cues("@offset two-seconds")
        assert "expected" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# stop sentinel
# ---------------------------------------------------------------------------

class TestStop:
    def test_stop_emits_fx_id_zero(self):
        f = parse_cues("03:00 stop")
        assert f.cues[0].fx_id == 0
        assert f.cues[0].kind == "fx"

    def test_stop_takes_no_arguments(self):
        with pytest.raises(CueParseError):
            parse_cues("03:00 stop 5")


class TestBpmCue:
    """Mid-track BPM change cue. Mutates the file-level default for
    FX cues that start at or after this point; already-running FXes
    keep the BPM they captured."""

    def test_parses_as_bpm_kind(self):
        f = parse_cues("00:30 bpm 138")
        c = f.cues[0]
        assert c.kind == "bpm"
        assert c.bpm == 138

    def test_requires_one_argument(self):
        with pytest.raises(CueParseError):
            parse_cues("00:30 bpm")
        with pytest.raises(CueParseError):
            parse_cues("00:30 bpm 120 138")

    def test_rejects_non_integer(self):
        with pytest.raises(CueParseError):
            parse_cues("00:30 bpm fast")

    def test_sorts_before_fx_at_same_time(self):
        # File-order has the FX before the bpm cue; sort must put
        # the bpm cue first so the FX picks up the new BPM.
        text = """
            00:15 sparkle_on_beat 255 0 0 100
            00:15 bpm 138
        """
        f = parse_cues(text)
        assert f.cues[0].kind == "bpm"
        assert f.cues[1].kind == "fx"

    def test_sorts_by_time_first(self):
        # A later bpm cue must NOT jump ahead of an earlier FX cue.
        text = """
            00:30 bpm 120
            00:15 sparkle_on_beat 100 100 100 50
        """
        f = parse_cues(text)
        assert f.cues[0].time_ms == 15_000
        assert f.cues[1].time_ms == 30_000

    def test_bpm_cue_picks_up_offset(self):
        # @offset applies to bpm cues just like FX cues.
        text = """
            @offset 1.5
            00:30 bpm 138
        """
        f = parse_cues(text)
        assert f.cues[0].time_ms == 31_500
        assert f.cues[0].kind == "bpm"


# ---------------------------------------------------------------------------
# Cue parsing
# ---------------------------------------------------------------------------

class TestCueParsing:
    def test_unknown_fx_name(self):
        with pytest.raises(CueParseError) as exc:
            parse_cues("00:30 wat_fx 1 2 3")
        assert "unknown FX" in str(exc.value)

    def test_positional_params_map_to_named_slots(self):
        # SparkleOnBeat: r, g, b, probability  (4 slots, no reserved).
        # probability is percent -> u8: 100% -> 255.
        f = parse_cues("00:30 sparkle_on_beat 80 200 200 100")
        c = f.cues[0]
        assert c.fx_id == 11
        assert c.params == (80, 200, 200, 255)

    def test_percent_converts_to_u8(self):
        f = parse_cues("00:30 sparkle_on_beat 0 0 0 50")
        # 50% -> round(50 * 255 / 100) == 128
        assert f.cues[0].params[3] == 128

    def test_partial_positional_leaves_unfilled_slots_zero(self):
        # SparkleOnBeat: r, g, b, probability. User supplies 2 values;
        # the rest stay 0 in the output tuple.
        f = parse_cues("00:00 sparkle_on_beat 80 200")
        c = f.cues[0]
        assert c.params == (80, 200, 0, 0)

    def test_too_many_positional_params(self):
        # SparkleOnBeat has 4 non-reserved slots; supplying 5 errors.
        with pytest.raises(CueParseError) as exc:
            parse_cues("00:30 sparkle_on_beat 1 2 3 4 5")
        assert "at most" in str(exc.value)

    def test_partial_positional_fills_from_left(self):
        # Only the first 2 named slots are populated; rest stay 0.
        f = parse_cues("00:30 sparkle_on_beat 100 200")
        assert f.cues[0].params == (100, 200, 0, 0)

    def test_param_out_of_range(self):
        with pytest.raises(CueParseError):
            parse_cues("00:30 sparkle_on_beat 1 2 3 200")  # 200% invalid


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

class TestFlags:
    def test_bpm_override(self):
        f = parse_cues("00:30 sparkle_on_beat 100 100 100 100 --bpm 140")
        assert f.cues[0].bpm == 140
        # positional still parses
        assert f.cues[0].params == (100, 100, 100, 255)

    def test_buildup_override(self):
        f = parse_cues("01:20 linear_buildup 255 0 0 100 64 --buildup 8")
        assert f.cues[0].buildup_s == 8

    def test_unknown_flag(self):
        with pytest.raises(CueParseError):
            parse_cues("00:30 sparkle_on_beat 1 2 3 50 --xyz 5")

    def test_flag_without_value(self):
        with pytest.raises(CueParseError):
            parse_cues("00:30 sparkle_on_beat 1 2 3 50 --bpm")

    def test_flag_then_positional_then_flag(self):
        # FadeToBlack: only 1 positional (start_master). Flags interleave.
        f = parse_cues("02:55 fade_to_black 200 --buildup 4 --bpm 138")
        c = f.cues[0]
        assert c.params == (200,)
        assert c.buildup_s == 4
        assert c.bpm == 138


# ---------------------------------------------------------------------------
# File-level behaviour
# ---------------------------------------------------------------------------

class TestFileLevel:
    def test_cues_sorted_by_time(self):
        text = """
            01:00 stop
            00:30 sparkle_on_beat 100 100 100 100
            00:00 quiet_wash 20 40 80
        """
        f = parse_cues(text)
        assert [c.time_ms for c in f.cues] == [0, 30_000, 60_000]

    def test_realistic_setlist(self):
        text = """
            # Coldplay - Fix You (demo)
            @artist Coldplay
            @title Fix You
            @bpm 138
            @default_fx quiet_wash 20 40 80

            00:00 quiet_wash      20  40 80
            # "When you try your best..."
            00:30 sparkle_on_beat 80  200 200 100
            00:35 sparkle_on_beat 255 0   255 100
            01:20 linear_buildup  255 0   0   100 64  --buildup 8
            01:28 strobe_burst    5   255
            01:30 sparkle_on_beat 255 255 255 100
            02:55 fade_to_black                          --buildup 4
            03:00 stop
        """
        f = parse_cues(text)
        assert f.artist == "Coldplay"
        assert f.title == "Fix You"
        assert f.default_bpm == 138
        assert f.default_fx_id == 1
        assert f.default_fx_params == (20, 40, 80, 0, 0)
        assert len(f.cues) == 8
        assert f.cues[-1].fx_id == 0
        assert f.cues[-2].buildup_s == 4

    def test_empty_file(self):
        f = parse_cues("")
        assert f.cues == []
        assert f.artist == ""

    def test_error_message_includes_line_number(self):
        text = "\n\n00:30 wat_fx 1 2 3\n"
        with pytest.raises(CueParseError) as exc:
            parse_cues(text)
        assert exc.value.line_no == 3
        assert "line 3" in str(exc.value)


# ---------------------------------------------------------------------------
# Lyric-anchor extraction
# ---------------------------------------------------------------------------

class TestLyricExtraction:
    def test_basic_lyric_comment(self):
        text = "# 00:30  When you try your best\n"
        f = parse_cues(text)
        assert len(f.lyrics) == 1
        assert f.lyrics[0] == Lyric(
            time_ms=30_000, text="When you try your best", line_no=1,
        )

    def test_fractional_lyric_time(self):
        text = "# 00:30.500  Centisecond grain\n"
        f = parse_cues(text)
        assert f.lyrics[0].time_ms == 30_500

    def test_lyric_with_hours(self):
        text = "# 1:02:30  Long song\n"
        f = parse_cues(text)
        assert f.lyrics[0].time_ms == (1 * 3600 + 2 * 60 + 30) * 1000

    def test_todo_skeleton_placeholder_skipped(self):
        # gen_cues_skeleton.py emits these; they're not real lyrics.
        text = (
            "# 00:30  Real lyric line\n"
            "# 00:30  TODO: cue here\n"
            "# 00:35  todo: lower-case still skipped\n"
            "# 00:40  TODO\n"
        )
        f = parse_cues(text)
        assert [l.text for l in f.lyrics] == ["Real lyric line"]

    def test_non_timestamped_comments_ignored(self):
        text = (
            "# --- Intro ---\n"
            "# (no time prefix here)\n"
            "# 00:30  Anchor\n"
        )
        f = parse_cues(text)
        assert len(f.lyrics) == 1
        assert f.lyrics[0].text == "Anchor"

    def test_lyrics_sorted_by_time(self):
        text = (
            "# 01:00  Second\n"
            "# 00:30  First\n"
        )
        f = parse_cues(text)
        assert [l.text for l in f.lyrics] == ["First", "Second"]

    def test_lyrics_alongside_cues(self):
        text = """
            @bpm 138
            # 00:13.40  When you try your best
            00:13.50 sparkle_on_beat 80 200 200 100
            # 00:20  but you don't succeed
            00:20 sparkle_on_beat 255 0 255 100
        """
        f = parse_cues(text)
        assert len(f.cues) == 2
        assert len(f.lyrics) == 2
        assert f.lyrics[0].text == "When you try your best"

    def test_no_lyrics_means_empty_list_not_error(self):
        text = "00:30 stop"
        f = parse_cues(text)
        assert f.lyrics == []


# ---------------------------------------------------------------------------
# Shipped cue files (Docs/songs/)
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from nocturnation_orchestrator.cues import parse_cues_file  # noqa: E402

_SONGS_DIR = Path(__file__).resolve().parent.parent.parent / "songs"


class TestShippedCueFiles:
    """Locks in that the committed `.cues` files in Docs/songs/ stay
    parseable. Catches drift between the FX library and any cue file
    that references it (renamed cue_name, removed FX, changed unit)."""

    @pytest.mark.parametrize(
        "path",
        sorted(_SONGS_DIR.glob("*.cues")),
        ids=lambda p: p.name,
    )
    def test_parses_clean(self, path):
        f = parse_cues_file(path)
        # default_fx must reference a registered FX if set.
        if f.default_fx_id:
            assert fx_registry.get(f.default_fx_id) is not None
        # Every cue's fx_id (besides 0 = stop) must be in the registry.
        for c in f.cues:
            if c.fx_id == 0:
                continue
            assert fx_registry.get(c.fx_id) is not None, (
                "%s line %d references unregistered fx_id %d"
                % (path.name, c.line_no, c.fx_id)
            )
