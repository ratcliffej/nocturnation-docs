"""Tests for the lyrics fetch / parse / skeleton renderer."""

import pytest

from nocturnation_orchestrator.lyrics import (
    LyricLine, LyricsError, fetch_lrc, parse_lrc, render_skeleton,
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

    def test_each_lyric_renders_as_comment(self):
        lines = [
            LyricLine(time_ms=30_000, text="When you try your best"),
            LyricLine(time_ms=65_000, text="Lights will guide you home"),
        ]
        body = render_skeleton("Coldplay", "Fix You", lines)
        assert "# 00:30  When you try your best" in body
        assert "# 01:05  Lights will guide you home" in body

    def test_no_todo_markers_emitted(self):
        # Skeleton output is just the lyric anchors; the LD adds real
        # cue lines between them. TODO markers were noise.
        lines = [
            LyricLine(time_ms=30_000, text="First"),
            LyricLine(time_ms=60_000, text="Second"),
            LyricLine(time_ms=90_000, text="Third"),
        ]
        body = render_skeleton("X", "Y", lines)
        assert "TODO" not in body
        # All three lyric lines present.
        assert "# 00:30  First" in body
        assert "# 01:00  Second" in body
        assert "# 01:30  Third" in body

    def test_no_synced_lyrics_renders_placeholder(self):
        body = render_skeleton("X", "Y", [])
        assert "(no synced lyrics found)" in body
