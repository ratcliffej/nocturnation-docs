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
        assert f.cues[0].params == (255, 0, 255, 255, 0)

    def test_tab_separated(self):
        f = parse_cues("00:30\tsparkle_on_beat\t255\t0\t255\t100")
        assert f.cues[0].params == (255, 0, 255, 255, 0)

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
        # quiet_wash declares 6 slots (5 + group); params tuple sized
        # to match.
        assert f.default_fx_params == (20, 40, 80, 0, 0, 0)

    def test_default_fx_no_params(self):
        f = parse_cues("@default_fx quiet_wash")
        assert f.default_fx_id == 1
        assert f.default_fx_params == (0, 0, 0, 0, 0, 0)

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
    def test_stop_resolves_to_blackout(self):
        # `stop` is a parser alias for the Blackout FX; the cue
        # carries Blackout's id, not 0. (Used to be 0 = cancel
        # sentinel; the cancel-only model left lights stuck on the
        # last LIGHT_WASH because dispatch stopped before sending the
        # zero-out frame. Blackout fixes by writing zeros for one
        # tick which the dispatcher pushes out.)
        f = parse_cues("03:00 stop")
        from nocturnation_orchestrator.fx.library.blackout import Blackout
        assert f.cues[0].fx_id == Blackout.id
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
        # SparkleOnBeat: r, g, b, probability, group  (5 slots).
        # probability is percent -> u8: 100% -> 255. group omitted -> 0.
        f = parse_cues("00:30 sparkle_on_beat 80 200 200 100")
        c = f.cues[0]
        assert c.fx_id == 11
        assert c.params == (80, 200, 200, 255, 0)

    def test_percent_converts_to_u8(self):
        f = parse_cues("00:30 sparkle_on_beat 0 0 0 50")
        # 50% -> round(50 * 255 / 100) == 128
        assert f.cues[0].params[3] == 128

    def test_partial_positional_leaves_unfilled_slots_zero(self):
        # SparkleOnBeat: r, g, b, probability, group. User supplies 2
        # values; the rest stay 0 in the output tuple.
        f = parse_cues("00:00 sparkle_on_beat 80 200")
        c = f.cues[0]
        assert c.params == (80, 200, 0, 0, 0)

    def test_too_many_positional_params(self):
        # SparkleOnBeat has 5 slots (r, g, b, probability, group);
        # supplying 6 errors.
        with pytest.raises(CueParseError) as exc:
            parse_cues("00:30 sparkle_on_beat 1 2 3 4 5 6")
        assert "at most" in str(exc.value)

    def test_partial_positional_fills_from_left(self):
        # Only the first 2 named slots are populated; rest stay 0.
        f = parse_cues("00:30 sparkle_on_beat 100 200")
        assert f.cues[0].params == (100, 200, 0, 0, 0)

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
        # positional still parses (5 slots: r, g, b, prob, group)
        assert f.cues[0].params == (100, 100, 100, 255, 0)

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
        # FadeToBlack: 2 positional slots (start_master, group).
        # Flags interleave with positional args cleanly.
        f = parse_cues("02:55 fade_to_black 200 --buildup 4 --bpm 138")
        c = f.cues[0]
        assert c.params == (200, 0)
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
        assert f.default_fx_params == (20, 40, 80, 0, 0, 0)
        assert len(f.cues) == 8
        from nocturnation_orchestrator.fx.library.blackout import Blackout
        assert f.cues[-1].fx_id == Blackout.id   # `stop` -> Blackout
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
        # cues_from_lyrics.py --comment-anchors mode emits these legacy
        # placeholders; they're not real lyrics.
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

    def test_no_space_after_hash_is_plain_comment(self):
        # `#00:45 cue_text` (no space between # and the timestamp)
        # is how the LD naturally comments out a cue line. It MUST
        # stay a plain comment, not be surfaced as a lyric.
        text = (
            "#00:45 drift_wash 200 200 200 10 10 100 10\n"
            "# 00:50 Real lyric text\n"
        )
        f = parse_cues(text)
        # Only the space-prefixed one is a lyric.
        assert len(f.lyrics) == 1
        assert f.lyrics[0].text == "Real lyric text"

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
            00:20 sparkle_on_beat 255 0 255 100 0
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


# ---------------------------------------------------------------------------
# Epic 13 B4: display-content cue lines + directives
# ---------------------------------------------------------------------------


class TestDisplayCueLines:
    def test_header_text(self):
        f = parse_cues("00:35 HeaderText: Coldplay")
        assert len(f.cues) == 1
        c = f.cues[0]
        assert c.kind == "header_text"
        assert c.time_ms == 35_000
        assert c.text == "Coldplay"

    def test_body_text(self):
        f = parse_cues("00:35 BodyText: Adventure of a Lifetime")
        assert len(f.cues) == 1
        c = f.cues[0]
        assert c.kind == "body_text"
        assert c.text == "Adventure of a Lifetime"

    def test_header_text_empty_clears_field(self):
        # Per user spec: empty after the colon clears that field on the Lume.
        f = parse_cues("00:36 HeaderText:")
        assert f.cues[0].kind == "header_text"
        assert f.cues[0].text == ""

    def test_body_text_empty_clears_field(self):
        f = parse_cues("00:36 BodyText:")
        assert f.cues[0].kind == "body_text"
        assert f.cues[0].text == ""

    def test_clearscreen(self):
        f = parse_cues("00:50 clearscreen")
        assert len(f.cues) == 1
        assert f.cues[0].kind == "clearscreen"
        assert f.cues[0].time_ms == 50_000

    def test_clearscreen_rejects_arguments(self):
        with pytest.raises(CueParseError):
            parse_cues("00:50 clearscreen text")

    def test_display_cue_lines_mix_with_fx_cues(self):
        # The shipped scheduler routes by cue.kind; the parser doesn't
        # care about adjacency between fx + display cues at the same
        # time, only that each line parses cleanly.
        f = parse_cues(
            "00:30 stop\n"
            "00:35 HeaderText: Coldplay\n"
            "00:35 BodyText: Adventure of a Lifetime\n"
            "01:00 clearscreen\n"
        )
        kinds = [c.kind for c in f.cues]
        assert kinds == ["fx", "header_text", "body_text", "clearscreen"]

    def test_body_text_preserves_punctuation(self):
        f = parse_cues("00:40 BodyText: Turn your magic on,  please!")
        # Token split + space-join collapses runs of whitespace - acceptable
        # for body text (cue files SHOULDN'T use leading/trailing spaces
        # for visual layout; Lume centres the line).
        assert f.cues[0].text == "Turn your magic on, please!"

    def test_body_text_newline_escape(self):
        # Literal `\n` in the cue file converts to actual newline char so
        # the Tildagon renderer can split on it for forced line breaks.
        f = parse_cues("00:03 BodyText: Music of\\nthe Spheres")
        assert f.cues[0].text == "Music of\nthe Spheres"

    def test_body_text_multiple_newlines(self):
        # Several `\n` escapes in a row each become newline chars; the
        # renderer turns consecutive newlines into blank lines for
        # vertical spacing.
        f = parse_cues("00:03 BodyText: Line1\\nLine2\\n\\nLine4")
        assert f.cues[0].text == "Line1\nLine2\n\nLine4"

    def test_header_text_newline_escape(self):
        # Same escape applies to HeaderText for consistency, though
        # the marquee renderer typically only shows one logical line.
        f = parse_cues("00:01 HeaderText: Coldplay\\n2026 Tour")
        assert f.cues[0].text == "Coldplay\n2026 Tour"


class TestDisplayDirectives:
    def test_show_song_info_bare(self):
        f = parse_cues("@ShowSongInfo\n00:00 stop")
        assert f.show_song_info is True
        assert f.show_bitmap is False  # default unchanged

    def test_show_song_info_true(self):
        f = parse_cues("@ShowSongInfo true\n00:00 stop")
        assert f.show_song_info is True

    def test_show_song_info_false(self):
        f = parse_cues("@ShowSongInfo false\n00:00 stop")
        assert f.show_song_info is False

    def test_show_bitmap_bare(self):
        f = parse_cues("@ShowBitmap\n00:00 stop")
        assert f.show_bitmap is True

    def test_show_song_info_default_off(self):
        # No directive -> default False (back-compat: existing cue files
        # that don't opt in see no behaviour change).
        f = parse_cues("00:00 stop")
        assert f.show_song_info is False
        assert f.show_bitmap is False

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


# ---------------------------------------------------------------------------
# Epic 14 B7-rest: @during + @palette directives
# ---------------------------------------------------------------------------


class TestDuringDirective:
    """`@during <section_name> <fx> [args...]` expands at parse time
    into a cue at the named section's start_ms."""

    def _parse(self, text):
        return parse_cues(text, registry=fx_registry)

    def test_during_expands_to_cue_at_section_start(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@section verse1 0:11.50 0:35.00\n"
            "@during verse1 quiet_wash 200 60 130\n"
        )
        f = self._parse(text)
        assert len(f.cues) == 1
        assert f.cues[0].time_ms == 11_500
        assert f.cues[0].kind == "fx"
        # FX is quiet_wash (id 1 in the canonical registry).
        assert f.cues[0].fx_id == 1
        # Pending list cleared after resolution.
        assert f.pending_during == []

    def test_during_resolves_when_directive_appears_before_section(self):
        # @during BEFORE @section in file order - post-parse resolution
        # means this still works.
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@during chorus1 sparkle_on_beat 255 100 50 80 0\n"
            "@section chorus1 0:35.00 0:55.70\n"
        )
        f = self._parse(text)
        assert len(f.cues) == 1
        assert f.cues[0].time_ms == 35_000

    def test_during_unknown_section_warns_but_continues(self, capsys):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@section verse1 0:00 0:30\n"
            "@during chorus1 quiet_wash 200 60 130\n"   # typo / no such section
        )
        f = self._parse(text)
        # No cue emitted for the unknown section.
        assert f.cues == []
        # Warning went to stderr.
        err = capsys.readouterr().err
        assert "chorus1" in err
        assert "verse1" in err   # known sections listed for the operator

    def test_multiple_during_per_section(self):
        # Multiple @during directives all expand. Two FX at the same
        # time_ms is fine - the scheduler walks them in order; the
        # later one wins per the runner's "most recent FX" policy.
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@section verse1 0:11.50 0:35.00\n"
            "@during verse1 quiet_wash 200 60 130\n"
            "@during verse1 sparkle_on_beat 100 200 50 70 0\n"
        )
        f = self._parse(text)
        assert len(f.cues) == 2
        assert all(c.time_ms == 11_500 for c in f.cues)

    def test_during_too_few_tokens_raises(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@section verse1 0:00 0:30\n"
            "@during verse1\n"   # missing FX name
        )
        with pytest.raises(Exception):
            self._parse(text)

    def test_during_unknown_fx_raises(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@section verse1 0:00 0:30\n"
            "@during verse1 not_an_fx 1 2 3\n"
        )
        with pytest.raises(Exception):
            self._parse(text)


class TestPaletteDirective:
    """`@palette <name> #RRGGBB,#RRGGBB,...` captures named colour
    lists in `file.palettes`. Placeholder expansion is covered by
    `TestPalettePlaceholders` below."""

    def _parse(self, text):
        return parse_cues(text, registry=fx_registry)

    def test_simple_palette(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d #FF0000,#FF8800,#FFFF00\n"
        )
        f = self._parse(text)
        assert "stage_d" in f.palettes
        assert f.palettes["stage_d"] == [
            (0xFF, 0x00, 0x00),
            (0xFF, 0x88, 0x00),
            (0xFF, 0xFF, 0x00),
        ]

    def test_palette_with_spaces_after_commas(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d #FF0000, #FF8800, #FFFF00\n"
        )
        f = self._parse(text)
        assert f.palettes["stage_d"] == [
            (0xFF, 0x00, 0x00),
            (0xFF, 0x88, 0x00),
            (0xFF, 0xFF, 0x00),
        ]

    def test_palette_without_hash_prefix(self):
        # `RRGGBB` (no leading #) is tolerated.
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d FF0000,FF8800\n"
        )
        f = self._parse(text)
        assert f.palettes["stage_d"] == [
            (0xFF, 0x00, 0x00),
            (0xFF, 0x88, 0x00),
        ]

    def test_palette_with_garbled_entries_skipped(self):
        # One bad entry doesn't kill the palette - just that entry
        # gets dropped.
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d #FF0000,notahex,#FFFF00\n"
        )
        f = self._parse(text)
        # 'notahex' filtered out.
        assert f.palettes["stage_d"] == [
            (0xFF, 0x00, 0x00),
            (0xFF, 0xFF, 0x00),
        ]

    def test_palette_redefinition_overwrites(self):
        # Last @palette with the same name wins.
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d #FF0000\n"
            "@palette stage_d #00FF00,#0000FF\n"
        )
        f = self._parse(text)
        assert f.palettes["stage_d"] == [
            (0x00, 0xFF, 0x00),
            (0x00, 0x00, 0xFF),
        ]

    def test_multiple_palettes(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d #FF0000\n"
            "@palette artist_x #00FF00\n"
        )
        f = self._parse(text)
        assert set(f.palettes.keys()) == {"stage_d", "artist_x"}

    def test_palette_too_few_tokens_raises(self):
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette\n"   # missing name AND colours
        )
        with pytest.raises(Exception):
            self._parse(text)

    def test_palette_only_garbled_entries_drops_the_palette(self):
        # If NONE of the entries parse, the palette name isn't stored.
        text = (
            "@artist X\n@title Y\n@bpm 178\n"
            "@palette stage_d notahex,alsobad\n"
        )
        f = self._parse(text)
        assert "stage_d" not in f.palettes


class TestPalettePlaceholders:
    """Epic 14.9 Block A. `@name[idx]` placeholders in cue lines /
    @during args expand to the three RGB tokens of the named palette's
    idx-th colour, before directive vs cue dispatch.

    Forward references are allowed: the parser scans @palette
    directives first, then processes the body. This means a palette
    declared at the foot of the file is still usable for cues at the
    top - useful for authoring conventions that group all palette
    declarations together.
    """

    def _parse(self, text):
        return parse_cues(text, registry=fx_registry)

    def test_basic_substitution_on_cue_line(self):
        text = (
            "@palette pal #504028,#823C20\n"
            "0:05.00 quiet_wash @pal[0]\n"
        )
        f = self._parse(text)
        assert len(f.cues) == 1
        assert f.cues[0].params_raw[:3] == (0x50, 0x40, 0x28)

    def test_substitution_in_during_directive(self):
        text = (
            "@palette chorus #FF0000,#00FF00\n"
            "@section verse1  0:11.50 0:35.00\n"
            "@during verse1 quiet_wash @chorus[1]\n"
        )
        f = self._parse(text)
        assert len(f.cues) == 1
        assert f.cues[0].params_raw[:3] == (0, 0xFF, 0)
        assert f.cues[0].time_ms == 11_500

    def test_forward_reference_works(self):
        # Cue at line 3 references palette declared on line 4.
        text = (
            "\n"
            "0:05.00 quiet_wash @later[0]\n"
            "@palette later #102030\n"
        )
        f = self._parse(text)
        assert f.cues[0].params_raw[:3] == (0x10, 0x20, 0x30)

    def test_multiple_placeholders_on_one_line(self):
        text = (
            "@palette pal #112233,#445566\n"
            "0:05.00 drift_wash @pal[0] @pal[1] 60\n"
        )
        f = self._parse(text)
        # drift_wash params: a_r a_g a_b b_r b_g b_b cycle (group)
        assert f.cues[0].params_raw[:6] == (
            0x11, 0x22, 0x33, 0x44, 0x55, 0x66,
        )
        assert f.cues[0].params_raw[6] == 60

    def test_mixed_inline_rgb_and_placeholder(self):
        text = (
            "@palette pal #112233\n"
            "0:05.00 drift_wash @pal[0] 50 60 70 80\n"
        )
        f = self._parse(text)
        # First three from palette, next three inline, then cycle=80.
        assert f.cues[0].params_raw[:7] == (0x11, 0x22, 0x33, 50, 60, 70, 80)

    def test_unknown_palette_raises(self):
        text = (
            "@palette p #112233\n"
            "0:00 quiet_wash @nope[0]\n"
        )
        with pytest.raises(CueParseError) as excinfo:
            self._parse(text)
        msg = str(excinfo.value)
        assert "unknown palette 'nope'" in msg
        # Helpful: list known palette names so the typo is fixable
        # without grepping for declarations.
        assert "known: p" in msg

    def test_out_of_range_index_raises(self):
        text = (
            "@palette p #112233\n"
            "0:00 quiet_wash @p[5]\n"
        )
        with pytest.raises(CueParseError) as excinfo:
            self._parse(text)
        msg = str(excinfo.value)
        assert "index 5 out of range" in msg
        # Tell the author the valid range so they don't have to count.
        assert "valid 0..0" in msg

    def test_no_palettes_declared_message(self):
        text = "0:00 quiet_wash @nope[0]\n"
        with pytest.raises(CueParseError) as excinfo:
            self._parse(text)
        assert "(none declared)" in str(excinfo.value)

    def test_placeholder_inside_param_string_is_not_expanded(self):
        # `foo@bar[0]` (no leading `@` after a separator) is not a
        # placeholder. The regex anchors to the whole token; this
        # token has no leading `@` so it stays put.
        text = (
            "@palette pal #112233\n"
            "0:00 quiet_wash 50 50 50\n"
            # Verify the placeholder regex needs the @ at token start
            # by sending an unparseable token in. Should raise the
            # usual int-parse error, NOT a palette error.
        )
        f = self._parse(text)
        assert "pal" in f.palettes
        # Sanity: regex doesn't match a non-anchored case.
        from nocturnation_orchestrator.cues import _PALETTE_PLACEHOLDER_RE
        assert _PALETTE_PLACEHOLDER_RE.match("foo@bar[0]") is None
        assert _PALETTE_PLACEHOLDER_RE.match("@bar[0]") is not None


class TestAnalysisSidecarLoading:
    """Epic 14.9 Block B. parse_cues_file opportunistically loads
    `<basename>.cues.analysis.json` into CueFile.beats_ms when one
    exists. Sidecar absence or corruption is benign - empty list
    falls the FX layer back to the pre-14.9 bpm clock."""

    def test_no_sidecar_leaves_beats_empty(self, tmp_path):
        cuefile = tmp_path / "song.cues"
        cuefile.write_text("@artist X\n@title Y\n@bpm 120\n")
        f = parse_cues_file(str(cuefile))
        assert f.beats_ms == []

    def test_sidecar_loaded_and_converted_to_ms(self, tmp_path):
        import json
        cuefile = tmp_path / "song.cues"
        cuefile.write_text("@artist X\n@title Y\n@bpm 138\n")
        sidecar = tmp_path / "song.cues.analysis.json"
        sidecar.write_text(json.dumps({
            "tempo": 138.0,
            "beats": [1.207, 1.637, 2.078, 2.508],
            "time_sig": 4,
        }))
        f = parse_cues_file(str(cuefile))
        assert f.beats_ms == [1207, 1637, 2078, 2508]

    def test_corrupt_sidecar_silently_falls_back(self, tmp_path):
        cuefile = tmp_path / "song.cues"
        cuefile.write_text("@artist X\n@title Y\n@bpm 120\n")
        sidecar = tmp_path / "song.cues.analysis.json"
        sidecar.write_text("not valid json {{{")
        f = parse_cues_file(str(cuefile))
        # Bad JSON shouldn't kill the parse. Empty beats_ms = bpm
        # fallback behaviour for the FX layer.
        assert f.beats_ms == []

    def test_sidecar_missing_beats_key_falls_back(self, tmp_path):
        import json
        cuefile = tmp_path / "song.cues"
        cuefile.write_text("@artist X\n@title Y\n@bpm 120\n")
        sidecar = tmp_path / "song.cues.analysis.json"
        sidecar.write_text(json.dumps({"tempo": 120.0}))
        f = parse_cues_file(str(cuefile))
        assert f.beats_ms == []

    def test_sidecar_beats_sorted_ascending(self, tmp_path):
        # Defensive: if the sidecar somehow has out-of-order beats
        # (manual edit, future MIR tool quirk), we sort before use
        # so the binary search in the FX is correct.
        import json
        cuefile = tmp_path / "song.cues"
        cuefile.write_text("@bpm 120\n")
        sidecar = tmp_path / "song.cues.analysis.json"
        sidecar.write_text(json.dumps({"beats": [2.5, 1.0, 1.5, 0.5]}))
        f = parse_cues_file(str(cuefile))
        assert f.beats_ms == [500, 1000, 1500, 2500]


class TestBarsValueSuffix:
    """Epic 14.9 Block C. A value of shape `Nb` or `N.5b` on a `100ms`
    param means "N bars at the file's current @bpm + @time_sig".

      slider = round(N * time_sig * 60000 / bpm / 100)

    Tested via drift_wash's `cycle` param (only 100ms-typed slot in
    the shipping FX library at the time of writing). Clamps to
    0..255 (the 100ms slider range). Defaults are 120 BPM 4/4 if
    @bpm or @time_sig is missing.
    """

    def _parse(self, text):
        return parse_cues(text, registry=fx_registry)

    def _cycle_of(self, f):
        # drift_wash has cycle at param slot 6.
        return f.cues[0].params[6]

    def test_120_4_one_bar_is_slider_20(self):
        # 1 bar at 120 BPM 4/4 = 4 * 500 ms = 2000 ms = slider 20.
        f = self._parse(
            "@bpm 120\n@time_sig 4\n"
            "0:00 drift_wash 10 20 30 40 50 60 1b\n"
        )
        assert self._cycle_of(f) == 20

    def test_138_4_four_bars_is_slider_70(self):
        # 4 bars at 138 BPM 4/4 = 4 * 4 * (60000/138) = 6956.5 ms
        # slider = round(6956.5 / 100) = 70.
        f = self._parse(
            "@bpm 138\n@time_sig 4\n"
            "0:00 drift_wash 10 20 30 40 50 60 4b\n"
        )
        assert self._cycle_of(f) == 70

    def test_fractional_bars(self):
        # 0.5b at 120 BPM 4/4 = 1000 ms = slider 10.
        f = self._parse(
            "@bpm 120\n@time_sig 4\n"
            "0:00 drift_wash 10 20 30 40 50 60 0.5b\n"
        )
        assert self._cycle_of(f) == 10

    def test_3_4_time_sig(self):
        # 2b at 120 BPM 3/4 = 2 * 3 * 500 = 3000 ms = slider 30.
        f = self._parse(
            "@bpm 120\n@time_sig 3\n"
            "0:00 drift_wash 10 20 30 40 50 60 2b\n"
        )
        assert self._cycle_of(f) == 30

    def test_bars_on_non_100ms_param_raises(self):
        # Bars suffix on an RGB (u8) param is a typo - reject loudly.
        with pytest.raises(CueParseError) as excinfo:
            self._parse(
                "@bpm 120\n@time_sig 4\n"
                "0:00 quiet_wash 1b 20 30\n"
            )
        msg = str(excinfo.value)
        assert "'1b' (bars)" in msg
        assert "only valid on 100ms params" in msg

    def test_missing_bpm_defaults_to_120(self):
        # No @bpm + no @time_sig: defaults of 120 + 4 apply so the
        # value still parses (operator approximately gets what they
        # meant; explicit directives pin it exactly).
        f = self._parse("0:00 drift_wash 10 20 30 40 50 60 1b\n")
        assert self._cycle_of(f) == 20

    def test_raw_value_preserves_bars_notation(self):
        # params_raw is used by the debug log; should show "4b" not
        # the slider-encoded number so the operator sees what they
        # actually wrote.
        f = self._parse(
            "@bpm 120\n@time_sig 4\n"
            "0:00 drift_wash 10 20 30 40 50 60 4b\n"
        )
        assert f.cues[0].params_raw[6] == "4b"

    def test_zero_bars_is_zero(self):
        f = self._parse(
            "@bpm 120\n@time_sig 4\n"
            "0:00 drift_wash 10 20 30 40 50 60 0b\n"
        )
        assert self._cycle_of(f) == 0

    def test_very_large_bars_clamps_at_255(self):
        # 30 bars at 60 BPM 4/4 = 120 s = slider 1200, clamps to 255.
        f = self._parse(
            "@bpm 60\n@time_sig 4\n"
            "0:00 drift_wash 10 20 30 40 50 60 30b\n"
        )
        assert self._cycle_of(f) == 255
