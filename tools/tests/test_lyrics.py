"""Tests for the lyrics fetch / parse / skeleton renderer."""

import pytest

from nocturnation_orchestrator.lyrics import (
    LyricLine, LyricsError, detect_non_latin_scripts,
    fetch_lrc, parse_lrc, render_skeleton,
)


# ---------------------------------------------------------------------------
# fetch_lrc (HTTP injection)
# ---------------------------------------------------------------------------

class TestFetchLrc:
    def test_happy_path_returns_synced_lyrics(self):
        def fake_get(url, timeout):
            assert "lrclib.net" in url
            assert "Coldplay" in url
            return 200, (
                b'{"syncedLyrics": "[00:30.00] When you try your best\\n",'
                b' "plainLyrics": "When you try your best"}'
            )
        lrc = fetch_lrc("Coldplay", "Fix You", http_get=fake_get)
        assert "[00:30.00]" in lrc
        assert "When you try" in lrc

    def test_404_raises(self):
        def fake_get(url, timeout):
            return 404, b""
        with pytest.raises(LyricsError) as exc:
            fetch_lrc("Nobody", "Nothing", http_get=fake_get)
        assert "no entry" in str(exc.value)

    def test_non_200_raises(self):
        def fake_get(url, timeout):
            return 503, b""
        with pytest.raises(LyricsError) as exc:
            fetch_lrc("X", "Y", http_get=fake_get)
        assert "503" in str(exc.value)

    def test_no_synced_lyrics_raises(self):
        def fake_get(url, timeout):
            return 200, b'{"plainLyrics": "..."}'
        with pytest.raises(LyricsError) as exc:
            fetch_lrc("X", "Y", http_get=fake_get)
        assert "no synced" in str(exc.value)

    def test_bad_json_raises(self):
        def fake_get(url, timeout):
            return 200, b"not json"
        with pytest.raises(LyricsError):
            fetch_lrc("X", "Y", http_get=fake_get)

    def test_empty_args_rejected(self):
        with pytest.raises(LyricsError):
            fetch_lrc("", "Title", http_get=lambda *a: (200, b"{}"))
        with pytest.raises(LyricsError):
            fetch_lrc("Artist", "", http_get=lambda *a: (200, b"{}"))


# ---------------------------------------------------------------------------
# parse_lrc
# ---------------------------------------------------------------------------

class TestParseLrc:
    def test_basic_lines(self):
        text = (
            "[00:30.50] When you try your best\n"
            "[01:05.00] Lights will guide you home\n"
        )
        lines = parse_lrc(text)
        assert len(lines) == 2
        assert lines[0].time_ms == 30_500
        assert lines[0].text == "When you try your best"
        assert lines[1].time_ms == 65_000

    def test_handles_missing_centiseconds(self):
        text = "[00:30] Whole second\n"
        lines = parse_lrc(text)
        assert lines == [LyricLine(time_ms=30_000, text="Whole second")]

    def test_skips_metadata_only_lines(self):
        text = (
            "[ti:Fix You]\n"
            "[ar:Coldplay]\n"
            "[00:30.00] Lyric\n"
        )
        lines = parse_lrc(text)
        # Metadata lines have non-numeric mm so the regex skips them;
        # the only kept line is the timestamped lyric.
        assert lines == [LyricLine(time_ms=30_000, text="Lyric")]

    def test_multiple_timestamps_per_line(self):
        # Some LRC files repeat one line under several stamps.
        text = "[00:30.00][01:00.00] Lights\n"
        lines = parse_lrc(text)
        assert [l.time_ms for l in lines] == [30_000, 60_000]
        assert all(l.text == "Lights" for l in lines)

    def test_output_sorted(self):
        text = (
            "[01:00.00] Second\n"
            "[00:30.00] First\n"
        )
        lines = parse_lrc(text)
        assert [l.text for l in lines] == ["First", "Second"]

    def test_blank_lines_skipped(self):
        text = "\n[00:10.00] Word\n\n\n[00:20.00] Word2\n"
        lines = parse_lrc(text)
        assert len(lines) == 2


# ---------------------------------------------------------------------------
# render_skeleton
# ---------------------------------------------------------------------------

class TestRenderSkeleton:
    def test_header_directives_present(self):
        lines = [LyricLine(time_ms=30_000, text="Lyric")]
        body = render_skeleton(
            "Coldplay", "Fix You", lines,
            default_bpm=138, default_fx="quiet_wash",
            default_fx_params="20 40 80",
        )
        assert "@artist     Coldplay" in body
        assert "@title      Fix You" in body
        assert "@bpm        138" in body
        assert "@default_fx quiet_wash 20 40 80" in body

    def test_each_lyric_renders_as_bodytext_cue(self):
        # Epic 14 B1: default output emits real BodyText: cues at
        # centisecond precision, not comment anchors. Lyrics render
        # on Lume LCDs immediately - author doesn't have to convert.
        lines = [
            LyricLine(time_ms=30_500, text="When you try your best"),
            LyricLine(time_ms=65_120, text="Lights will guide you home"),
        ]
        body = render_skeleton("Coldplay", "Fix You", lines)
        assert "00:30.50  BodyText: When you try your best" in body
        assert "01:05.12  BodyText: Lights will guide you home" in body

    def test_no_todo_markers_emitted(self):
        # Skeleton output is just the lyric anchors; the LD adds real
        # FX cues between them. TODO markers were noise.
        lines = [
            LyricLine(time_ms=30_000, text="First"),
            LyricLine(time_ms=60_000, text="Second"),
            LyricLine(time_ms=90_000, text="Third"),
        ]
        body = render_skeleton("X", "Y", lines)
        assert "TODO" not in body
        assert "00:30.00  BodyText: First" in body
        assert "01:00.00  BodyText: Second" in body
        assert "01:30.00  BodyText: Third" in body

    def test_comment_anchors_legacy_mode(self):
        # Pre-Epic-13 behaviour: lyrics as `# comment` anchors. Author
        # opts in via comment_anchors=True if they want to hand-author
        # the BodyText: cues themselves.
        lines = [LyricLine(time_ms=30_000, text="Lyric")]
        body = render_skeleton("X", "Y", lines, comment_anchors=True)
        assert "# 00:30  Lyric" in body
        assert "BodyText:" not in body

    def test_no_synced_lyrics_renders_placeholder(self):
        body = render_skeleton("X", "Y", [])
        assert "(no synced lyrics found)" in body

    def test_non_latin_warning_in_skeleton(self):
        # Lyrics with Hangul trigger an in-file warning block. The
        # author sees the warning when they open the cue file +
        # knows which lines need romanising before show time.
        lines = [
            LyricLine(time_ms=30_000, text="Hello"),
            LyricLine(time_ms=35_000, text="나를 밝혀주는 건"),  # Korean
        ]
        body = render_skeleton("X", "Y", lines)
        assert "WARNING: lyrics contain non-Latin script(s)" in body
        assert "Hangul" in body
        # Lyrics still rendered as cues - the warning is advisory,
        # not a refusal to emit.
        assert "BodyText:" in body

    def test_no_warning_for_pure_latin(self):
        lines = [LyricLine(time_ms=30_000, text="Plain English lyric")]
        body = render_skeleton("X", "Y", lines)
        assert "WARNING" not in body
        # Pure-Latin English + accented Latin characters don't warn.
        lines = [LyricLine(time_ms=30_000, text="César with diacritic")]
        body = render_skeleton("X", "Y", lines)
        assert "WARNING" not in body


# ---------------------------------------------------------------------------
# detect_non_latin_scripts (Epic 14 B1)
# ---------------------------------------------------------------------------

class TestDetectNonLatinScripts:
    def test_pure_latin_returns_empty(self):
        assert detect_non_latin_scripts("Hello, world!") == set()
        assert detect_non_latin_scripts("") == set()

    def test_latin_with_diacritics_returns_empty(self):
        # Latin-1 supplement + Latin Extended-A cover standard
        # European diacritics; these aren't "non-Latin" for our purposes.
        assert detect_non_latin_scripts("Café naïve résumé") == set()
        assert detect_non_latin_scripts("łodź Lodź") == set()

    def test_hangul_detected(self):
        # Korean lyric (random sample): "Star woven from your love"
        assert "Hangul" in detect_non_latin_scripts("너란 사랑으로")

    def test_hangul_jamo_detected(self):
        # Hangul Jamo block (U+1100-U+11FF) - rarely used but valid.
        assert "Hangul" in detect_non_latin_scripts("각")

    def test_cjk_detected(self):
        assert "CJK" in detect_non_latin_scripts("你好")   # Chinese "ni hao"

    def test_hiragana_detected(self):
        assert "Hiragana" in detect_non_latin_scripts("こんにちは")  # Japanese

    def test_katakana_detected(self):
        assert "Katakana" in detect_non_latin_scripts("サクラ")

    def test_cyrillic_detected(self):
        assert "Cyrillic" in detect_non_latin_scripts("Привет")

    def test_greek_detected(self):
        assert "Greek" in detect_non_latin_scripts("γεια")

    def test_arabic_detected(self):
        assert "Arabic" in detect_non_latin_scripts("مرحبا")

    def test_mixed_english_and_korean(self):
        # Realistic case: a K-pop chorus that mixes English and Korean.
        scripts = detect_non_latin_scripts("Never-ending forever, 너와 함께")
        assert scripts == {"Hangul"}

    def test_multiple_non_latin_scripts(self):
        text = "나 你 П"   # Hangul + CJK + Cyrillic in one string
        scripts = detect_non_latin_scripts(text)
        assert scripts == {"Hangul", "CJK", "Cyrillic"}
