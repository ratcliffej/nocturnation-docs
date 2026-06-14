"""Orchestrator tests (Epic 10 B5).

Covers the testable pieces:
- nowplaying.macos output parsing (no subprocess, injected runner)
- output.artnet packet framing
- matcher slug + fallback
- scheduler position tracking + seek handling
- main.run smoke (synthetic backend, capture dispatch)

The hardware-dependent edges (real serial port, real nowplaying-cli)
are validated at the bench, not in pytest.
"""

import socket
import threading

import pytest

from nocturnation_orchestrator.cues import parse_cues
from nocturnation_orchestrator.fx import library  # noqa: F401  side-effects
from nocturnation_orchestrator.main import run
from nocturnation_orchestrator.matcher import find_cue_path, slugify
from nocturnation_orchestrator.nowplaying import NowPlaying
from nocturnation_orchestrator.nowplaying.macos import (
    MacOSBackend, _parse_raw_output,
)
from nocturnation_orchestrator.output.artnet import (
    ArtnetDispatcher, build_artdmx_packet,
)
from nocturnation_orchestrator.scheduler import (
    CueScheduler, PositionTracker,
)


# ---------------------------------------------------------------------------
# Now-playing: macOS output parser
# ---------------------------------------------------------------------------

_SAMPLE_RAW = """
{
  "kMRMediaRemoteNowPlayingInfoTitle" : "Fix You",
  "kMRMediaRemoteNowPlayingInfoArtist" : "Coldplay",
  "kMRMediaRemoteNowPlayingInfoElapsedTime" : 30.5,
  "kMRMediaRemoteNowPlayingInfoDuration" : 295,
  "kMRMediaRemoteNowPlayingInfoPlaybackRate" : 1.0
}
"""


class TestMacOSParseRawOutput:
    def test_basic_playing(self):
        np = _parse_raw_output(_SAMPLE_RAW)
        assert np is not None
        assert np.is_playing
        assert np.artist == "Coldplay"
        assert np.title == "Fix You"
        assert np.position_ms == 30_500
        assert np.duration_ms == 295_000

    def test_paused_track(self):
        raw = _SAMPLE_RAW.replace(
            '"kMRMediaRemoteNowPlayingInfoPlaybackRate" : 1.0',
            '"kMRMediaRemoteNowPlayingInfoPlaybackRate" : 0',
        )
        np = _parse_raw_output(raw)
        assert np is not None
        assert np.is_playing is False

    def test_no_source_empty_stdout(self):
        assert _parse_raw_output("") is None
        assert _parse_raw_output("   \n") is None

    def test_no_source_empty_object(self):
        assert _parse_raw_output("{}") is None

    def test_malformed_json(self):
        # Defensive: a busted nowplaying-cli build shouldn't crash the loop.
        assert _parse_raw_output("not json") is None

    def test_real_world_sample(self):
        # Real MediaRemote output captured at the bench:
        # Coldplay / A Sky Full of Stars, paused at 52.97 s, 268 s total.
        raw = """
        {
          "kMRMediaRemoteNowPlayingInfoTitle" : "A Sky Full of Stars",
          "kMRMediaRemoteNowPlayingInfoArtist" : "Coldplay",
          "kMRMediaRemoteNowPlayingInfoAlbum" : "Ghost Stories",
          "kMRMediaRemoteNowPlayingInfoGenre" : "Alternative",
          "kMRMediaRemoteNowPlayingInfoElapsedTime" : 52.966817133,
          "kMRMediaRemoteNowPlayingInfoDuration" : 268,
          "kMRMediaRemoteNowPlayingInfoPlaybackRate" : 0,
          "kMRMediaRemoteNowPlayingInfoTrackNumber" : 8
        }
        """
        np = _parse_raw_output(raw)
        assert np is not None
        assert np.title == "A Sky Full of Stars"
        assert np.artist == "Coldplay"
        assert np.position_ms == 52_967
        assert np.duration_ms == 268_000
        assert np.genre == "Alternative"
        assert not np.is_playing

    def test_missing_genre_is_empty_string(self):
        # Older tracks / streaming sources may omit the genre key.
        # We should default to "" so the matcher's genre fallback
        # tier silently skips.
        raw = """
        {
          "kMRMediaRemoteNowPlayingInfoTitle" : "T",
          "kMRMediaRemoteNowPlayingInfoArtist" : "A",
          "kMRMediaRemoteNowPlayingInfoElapsedTime" : 0,
          "kMRMediaRemoteNowPlayingInfoDuration" : 100,
          "kMRMediaRemoteNowPlayingInfoPlaybackRate" : 1
        }
        """
        np = _parse_raw_output(raw)
        assert np.genre == ""


class TestMacOSBackendInjection:
    def test_poll_uses_injected_runner(self):
        captured_args = []

        def runner(args, timeout):
            captured_args.append(args)
            return _SAMPLE_RAW, 0

        backend = MacOSBackend(runner=runner)
        np = backend.poll()
        assert np is not None
        assert np.title == "Fix You"
        # The runner was invoked with the get-raw subcommand
        # (the workaround for the buggy per-field 'get').
        assert captured_args[0][1] == "get-raw"
        assert len(captured_args[0]) == 2


# ---------------------------------------------------------------------------
# Output: Art-Net packet framing
# ---------------------------------------------------------------------------

class TestArtdmxPacket:
    def test_header_layout(self):
        u = bytearray(512)
        u[0] = 0xAA
        u[511] = 0xBB
        pkt = build_artdmx_packet(u, sub_uni=3, net=1, sequence=7, physical=2)
        assert pkt[:8] == b"Art-Net\0"
        # opcode 0x5000 is little-endian on wire: 0x00, 0x50
        assert pkt[8] == 0x00
        assert pkt[9] == 0x50
        # protocol version 14, big-endian
        assert pkt[10] == 0x00
        assert pkt[11] == 0x0E
        assert pkt[12] == 7         # sequence
        assert pkt[13] == 2         # physical
        assert pkt[14] == 3         # sub_uni
        assert pkt[15] == 1         # net
        # length 512, big-endian
        assert pkt[16] == 0x02
        assert pkt[17] == 0x00
        # DMX data follows
        assert pkt[18] == 0xAA
        assert pkt[18 + 511] == 0xBB
        assert len(pkt) == 18 + 512

    def test_universe_size_validated(self):
        with pytest.raises(ValueError):
            build_artdmx_packet(bytearray(256))

    def test_dispatcher_sends_to_target(self):
        """Open a real UDP listener and send one universe; verify the
        bytes match."""
        recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recv_sock.bind(("127.0.0.1", 0))
        host, port = recv_sock.getsockname()
        recv_sock.settimeout(1.0)

        d = ArtnetDispatcher.open(host=host, port=port)
        try:
            u = bytearray(512)
            u[5] = 0xFF
            d.send(u)
            data, _addr = recv_sock.recvfrom(2048)
            assert data[:8] == b"Art-Net\0"
            assert data[18 + 5] == 0xFF
        finally:
            d.close()
            recv_sock.close()


# ---------------------------------------------------------------------------
# Matcher
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert slugify("Coldplay", "Fix You") == "coldplay-fix-you"

    def test_punctuation_collapsed(self):
        assert slugify("AC/DC", "T.N.T.") == "ac-dc-t-n-t"

    def test_unicode_folded(self):
        assert slugify("Sigur Rós", "Hoppípolla") == "sigur-ros-hoppipolla"

    def test_empty_parts_skipped(self):
        assert slugify("", "Fix You") == "fix-you"
        assert slugify("Coldplay", "") == "coldplay"
        assert slugify("", "") == ""


class TestFindCuePath:
    def test_per_track_match(self, tmp_path):
        (tmp_path / "coldplay-fix-you.cues").write_text("@bpm 120\n")
        p = find_cue_path(tmp_path, "Coldplay", "Fix You")
        assert p == tmp_path / "coldplay-fix-you.cues"

    def test_falls_back_to_default(self, tmp_path):
        (tmp_path / "_default.cues").write_text("@bpm 120\n")
        p = find_cue_path(tmp_path, "Unknown", "Track")
        assert p.name == "_default.cues"

    def test_returns_none_when_nothing(self, tmp_path):
        assert find_cue_path(tmp_path, "Anyone", "Anything") is None

    def test_unicode_artist(self, tmp_path):
        (tmp_path / "sigur-ros-hoppipolla.cues").write_text("@bpm 120\n")
        p = find_cue_path(tmp_path, "Sigur Rós", "Hoppípolla")
        assert p is not None and p.name == "sigur-ros-hoppipolla.cues"

    def test_per_genre_fallback(self, tmp_path):
        (tmp_path / "_default_metal.cues").write_text("@bpm 140\n")
        (tmp_path / "_default.cues").write_text("@bpm 120\n")
        # Unknown track + Metal genre -> the genre file wins over the
        # global default.
        p = find_cue_path(tmp_path, "Some Band", "Some Track", genre="Metal")
        assert p.name == "_default_metal.cues"

    def test_per_track_beats_per_genre(self, tmp_path):
        # A per-track file always wins over per-genre, even if both
        # exist - explicit programming overrides genre inference.
        (tmp_path / "some-band-some-track.cues").write_text("@bpm 120\n")
        (tmp_path / "_default_metal.cues").write_text("@bpm 140\n")
        p = find_cue_path(tmp_path, "Some Band", "Some Track", genre="Metal")
        assert p.name == "some-band-some-track.cues"

    def test_genre_falls_through_to_default(self, tmp_path):
        # Genre supplied but no per-genre file -> global default.
        (tmp_path / "_default.cues").write_text("@bpm 120\n")
        p = find_cue_path(
            tmp_path, "Unknown", "Track", genre="Synthwave",
        )
        assert p.name == "_default.cues"

    def test_multi_word_genre_slugified(self, tmp_path):
        # "Alternative Rock" -> "alternative-rock" -> file name uses
        # the hyphenated slug after the underscore prefix.
        (tmp_path / "_default_alternative-rock.cues").write_text("@bpm 120\n")
        p = find_cue_path(
            tmp_path, "Some Band", "Track", genre="Alternative Rock",
        )
        assert p.name == "_default_alternative-rock.cues"

    def test_empty_genre_skips_per_genre_tier(self, tmp_path):
        # The orchestrator never looks for `_default_.cues`.
        (tmp_path / "_default.cues").write_text("@bpm 120\n")
        p = find_cue_path(tmp_path, "Unknown", "Track", genre="")
        assert p.name == "_default.cues"


# ---------------------------------------------------------------------------
# Scheduler: position tracker
# ---------------------------------------------------------------------------

class TestPositionTracker:
    def test_interpolates_while_playing(self):
        t = PositionTracker()
        np = NowPlaying(True, "A", "B", position_ms=10_000, duration_ms=120_000)
        t.update_from_poll(np, now_ms=1_000)
        assert t.current_position(now_ms=1_500) == 10_500

    def test_does_not_advance_when_paused(self):
        t = PositionTracker()
        np = NowPlaying(False, "A", "B", position_ms=10_000, duration_ms=0)
        t.update_from_poll(np, now_ms=1_000)
        assert t.current_position(now_ms=10_000) == 10_000

    def test_clear_resets_play_state(self):
        t = PositionTracker()
        np = NowPlaying(True, "A", "B", position_ms=10_000, duration_ms=0)
        t.update_from_poll(np, now_ms=0)
        t.clear()
        assert not t.is_playing

    def test_stale_cache_does_not_reset_anchor(self):
        # MediaRemote sometimes returns the SAME elapsedTime on every
        # poll even while playing (it only refreshes the cached value
        # on state changes). The tracker must NOT re-anchor on a
        # no-change position, or wall-clock advancement gets pinned to
        # zero forever.
        t = PositionTracker()
        np = NowPlaying(True, "A", "B", position_ms=0, duration_ms=120_000)
        t.update_from_poll(np, now_ms=0)
        # 1 s later, OS still reports position=0. Tracker should keep
        # extrapolating from the original anchor instead of
        # resetting.
        t.update_from_poll(np, now_ms=1_000)
        assert t.current_position(now_ms=1_000) == 1_000
        # Another second.
        t.update_from_poll(np, now_ms=2_000)
        assert t.current_position(now_ms=2_000) == 2_000

    def test_genuine_position_change_reanchors(self):
        # When the OS reports a fresh position, absorb it (handles
        # seeks and well-behaved music apps).
        t = PositionTracker()
        t.update_from_poll(
            NowPlaying(True, "A", "B", position_ms=0, duration_ms=0),
            now_ms=0,
        )
        # 3 s later: OS catches up to ~3 s. Re-anchor.
        t.update_from_poll(
            NowPlaying(True, "A", "B", position_ms=3_000, duration_ms=0),
            now_ms=3_000,
        )
        assert t.current_position(now_ms=3_500) == 3_500
        # User scrubs forward to 30 s.
        t.update_from_poll(
            NowPlaying(True, "A", "B", position_ms=30_000, duration_ms=0),
            now_ms=5_000,
        )
        assert t.current_position(now_ms=5_500) == 30_500

    def test_play_pause_reanchors(self):
        # Pausing should freeze the position at whatever the OS reports
        # at that moment, not at wall-clock projection of the previous
        # anchor.
        t = PositionTracker()
        t.update_from_poll(
            NowPlaying(True, "A", "B", position_ms=0, duration_ms=0),
            now_ms=0,
        )
        # 10 s of wall clock, OS still says 0 (stale): we'd predict 10.
        # User then pauses; OS reports position=10000 (it finally
        # updated). Tracker should absorb the pause state and the
        # position.
        t.update_from_poll(
            NowPlaying(False, "A", "B", position_ms=10_000, duration_ms=0),
            now_ms=10_000,
        )
        assert not t.is_playing
        assert t.current_position(now_ms=20_000) == 10_000

    def test_track_change_reanchors(self):
        t = PositionTracker()
        t.update_from_poll(
            NowPlaying(True, "A", "Song 1", position_ms=60_000, duration_ms=0),
            now_ms=0,
        )
        # New track starts at position 0.
        t.update_from_poll(
            NowPlaying(True, "A", "Song 2", position_ms=0, duration_ms=0),
            now_ms=1_000,
        )
        assert t.current_position(now_ms=1_500) == 500


# ---------------------------------------------------------------------------
# Scheduler: cue cursor
# ---------------------------------------------------------------------------

class FakeRunner:
    """Recording double for FxRunner used by scheduler tests."""

    def __init__(self):
        self.calls = []
        self.cancels = []

    def start(self, fx_id, *, bpm=0, buildup_s=0, params=(0,)*6,
              position_ms=0, now_ms, replace_running=False):
        self.calls.append({
            "fx_id": fx_id, "bpm": bpm, "buildup_s": buildup_s,
            "params": params, "position_ms": position_ms, "now_ms": now_ms,
        })

    def cancel(self, now_ms):
        self.cancels.append(now_ms)


def _coldplay_cues():
    text = """
        @bpm 138
        @default_fx quiet_wash 20 40 80
        00:00 quiet_wash 20 40 80
        00:10 sparkle_on_beat 80 200 200 100
        00:20 sparkle_on_beat 255 0 255 100
        00:30 fade_to_black --buildup 4
        00:35 stop
    """
    return parse_cues(text)


class TestCueScheduler:
    def test_default_fx_fires_on_set(self):
        runner = FakeRunner()
        sched = CueScheduler(runner)
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        # Default FX should have started.
        assert runner.calls[-1]["fx_id"] == 1  # quiet_wash
        assert runner.cancels  # cancel was called as part of set

    def test_monotonic_advance_fires_each_cue(self):
        runner = FakeRunner()
        sched = CueScheduler(runner)
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        starts_before = len(runner.calls)
        sched.advance(0, now_ms=10)        # 00:00 cue (quiet_wash)
        sched.advance(10_000, now_ms=20)   # 00:10 cue (sparkle_on_beat)
        sched.advance(20_000, now_ms=30)   # 00:20 cue (sparkle_on_beat)
        fired_fx_ids = [c["fx_id"] for c in runner.calls[starts_before:]]
        assert fired_fx_ids == [1, 11, 11]

    def test_forward_seek_collapses_to_most_recent_cue(self):
        runner = FakeRunner()
        sched = CueScheduler(runner)
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        # Establish a baseline so the next jump is detected as a seek.
        sched.advance(0, now_ms=10)
        starts_before = len(runner.calls)
        # Jump from 0 -> 22 s; SHOULD fire the most recent cue at-or-before
        # 22 s (00:20 sparkle) but NOT the 00:10 sparkle.
        sched.advance(22_000, now_ms=1000)
        fired = [c["fx_id"] for c in runner.calls[starts_before:]]
        assert fired == [11]  # only the 00:20 cue

    def test_backward_seek_re_fires_landing_cue(self):
        runner = FakeRunner()
        sched = CueScheduler(runner)
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        # Play forward through all the cues.
        sched.advance(0, now_ms=10)
        sched.advance(10_000, now_ms=20)
        sched.advance(20_000, now_ms=30)
        starts_before = len(runner.calls)
        # User drags back to 12 s. Should re-fire the 00:10 cue.
        sched.advance(12_000, now_ms=100)
        fired = [c["fx_id"] for c in runner.calls[starts_before:]]
        assert fired == [11]

    def test_stop_cue_emits_cancel(self):
        runner = FakeRunner()
        sched = CueScheduler(runner)
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        sched.advance(35_000, now_ms=10_000)
        # The stop cue maps to fx_id=0 (which the runner treats as cancel).
        ids = [c["fx_id"] for c in runner.calls]
        assert 0 in ids

    def test_on_cue_fire_callback_invoked(self):
        # The debug observer should be called for every cue admission.
        runner = FakeRunner()
        observed = []
        sched = CueScheduler(
            runner,
            on_cue_fire=lambda cue, pos: observed.append((cue.fx_id, pos)),
        )
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        sched.advance(0, now_ms=10)
        sched.advance(10_000, now_ms=20)
        ids = [fx for fx, _pos in observed]
        # The 00:00 cue and 00:10 cue both fire; default_fx start does
        # NOT go through on_cue_fire (it's part of set_cue_file, not a
        # cue admission).
        assert 1 in ids and 11 in ids


class TestCueSchedulerLyrics:
    def test_lyric_callback_fires_in_time_order(self):
        runner = FakeRunner()
        observed = []
        sched = CueScheduler(
            runner,
            on_lyric=lambda lyric, pos: observed.append((lyric.text, pos)),
        )
        text = """
            @bpm 138
            # 00:10  First line
            # 00:20  Second line
            # 00:30  Third line
        """
        from nocturnation_orchestrator.cues import parse_cues
        sched.set_cue_file(parse_cues(text), now_ms=0)
        sched.advance(0, now_ms=10)
        sched.advance(10_000, now_ms=20)
        sched.advance(20_000, now_ms=30)
        sched.advance(30_000, now_ms=40)
        texts = [t for t, _pos in observed]
        assert texts == ["First line", "Second line", "Third line"]

    def test_forward_seek_collapses_lyrics(self):
        runner = FakeRunner()
        observed = []
        sched = CueScheduler(
            runner,
            on_lyric=lambda lyric, pos: observed.append(lyric.text),
        )
        text = """
            # 00:10  A
            # 00:20  B
            # 00:30  C
        """
        from nocturnation_orchestrator.cues import parse_cues
        sched.set_cue_file(parse_cues(text), now_ms=0)
        sched.advance(0, now_ms=10)
        observed.clear()
        # Big forward seek (>2 s threshold) to 25 s -> only C is fired.
        # Wait, 25 s is between B and C, so most-recent-at-or-before is B.
        sched.advance(25_000, now_ms=1000)
        assert observed == ["B"]

    def test_late_join_collapses_lyrics(self):
        # Operator presses play mid-song (or wall-clock interpolation
        # has already crossed several lyrics by the time the runner
        # gets ticked). The first advance() should fire only the most-
        # recent lyric, not dump every prior one in a burst.
        runner = FakeRunner()
        observed = []
        sched = CueScheduler(
            runner,
            on_lyric=lambda lyric, pos: observed.append(lyric.text),
        )
        text = """
            # 00:16  First
            # 00:24  Second
            # 00:32  Third
            # 00:55  Future
        """
        from nocturnation_orchestrator.cues import parse_cues
        sched.set_cue_file(parse_cues(text), now_ms=0)
        # First advance is at 53 s - we joined mid-song; collapse to
        # the most recent prior anchor (Third), don't fire First /
        # Second too.
        sched.advance(53_000, now_ms=100)
        assert observed == ["Third"]

    def test_late_join_collapses_cues(self):
        # Same as above but for cues - the audible analogue of the
        # 5-lyrics-at-once dump was 5-cues-at-once.
        runner = FakeRunner()
        sched = CueScheduler(runner)
        sched.set_cue_file(_coldplay_cues(), now_ms=0)
        # _coldplay_cues has cues at 0, 10s, 20s, 30s, 35s. Default fx
        # fires on set_cue_file. After that the runner has been called
        # once (the default_fx start). Joining at 25 s should add only
        # the cue at 20 s (most-recent prior).
        calls_before = len(runner.calls)
        sched.advance(25_000, now_ms=100)
        added = runner.calls[calls_before:]
        # Exactly one cue fires (the 20 s sparkle), not three.
        assert len(added) == 1
        assert added[0]["fx_id"] == 11   # sparkle_on_beat

    def test_late_join_at_zero_still_fires_zero_cue(self):
        # A genuine song-start (position 0 on first advance) is not a
        # late-join - we want the file's 00:00 cue to fire normally,
        # not be skipped.
        runner = FakeRunner()
        observed = []
        sched = CueScheduler(
            runner,
            on_lyric=lambda lyric, pos: observed.append(lyric.text),
        )
        text = """
            # 00:00  At the very start
            # 00:10  Later
        """
        from nocturnation_orchestrator.cues import parse_cues
        sched.set_cue_file(parse_cues(text), now_ms=0)
        sched.advance(0, now_ms=10)
        # Both the cue cursor and lyric cursor land on the 00:00
        # anchor - that's normal monotonic behaviour, not a seek.
        assert observed == ["At the very start"]

    def test_backward_seek_replays_landing_lyric(self):
        runner = FakeRunner()
        observed = []
        sched = CueScheduler(
            runner,
            on_lyric=lambda lyric, pos: observed.append(lyric.text),
        )
        text = """
            # 00:10  A
            # 00:20  B
            # 00:30  C
        """
        from nocturnation_orchestrator.cues import parse_cues
        sched.set_cue_file(parse_cues(text), now_ms=0)
        sched.advance(0, now_ms=10)
        sched.advance(35_000, now_ms=40)
        observed.clear()
        # Scrub back to 12 s -> re-fire landing lyric (A).
        sched.advance(12_000, now_ms=1000)
        assert observed == ["A"]


# ---------------------------------------------------------------------------
# Main loop smoke
# ---------------------------------------------------------------------------

class FakeBackend:
    def __init__(self, sequence):
        # sequence is a list of NowPlaying | None, popped one per poll.
        self._sequence = list(sequence)

    def poll(self):
        if not self._sequence:
            return None
        return self._sequence.pop(0)


class CaptureDispatcher:
    name = "capture"

    def __init__(self):
        self.sends = []
        self.closed = False

    def send(self, universe):
        # Take a snapshot - the universe is the same buffer on every tick.
        self.sends.append(bytes(universe))

    def close(self):
        self.closed = True


class TestMainLoopSmoke:
    def test_loop_skips_send_when_no_fx(self, tmp_path):
        # No songs dir, no source - the matcher returns None and the
        # scheduler stays stopped. The runner has no FX so we MUST
        # NOT dispatch the universe; sending an all-zero universe at
        # startup poisons the StickC mapper's wash seed.
        backend = FakeBackend([None])
        disp = CaptureDispatcher()
        clock = [0]
        def now_ms():
            return clock[0]
        def sleep(seconds):
            clock[0] += int(seconds * 1000) or 20
        run(
            nowplaying_backend=backend,
            dispatcher=disp,
            songs_dir=str(tmp_path),
            default_bpm=120,
            log=lambda *a, **kw: None,
            sleep=sleep,
            now_ms=now_ms,
            iteration_budget=5,
        )
        assert disp.closed
        # No FX was ever admitted - so no dispatch.
        assert disp.sends == []

    def test_static_wash_dispatches_only_once(self, tmp_path):
        # A static default_fx (QuietWash with constant params) should
        # produce exactly ONE USB dispatch across many ticks. Without
        # this suppression a long-running orchestrator floods USB at
        # 50 Hz with byte-identical frames.
        (tmp_path / "_default.cues").write_text(
            "@default_fx quiet_wash 100 100 100\n"
        )
        backend = FakeBackend([
            NowPlaying(True, "A", "B", position_ms=0, duration_ms=10_000),
        ])
        disp = CaptureDispatcher()
        clock = [0]
        def now_ms():
            return clock[0]
        def sleep(seconds):
            clock[0] += max(int(seconds * 1000), 20)
        run(
            nowplaying_backend=backend, dispatcher=disp,
            songs_dir=str(tmp_path), default_bpm=120,
            log=lambda *a, **kw: None,
            sleep=sleep, now_ms=now_ms,
            iteration_budget=200,
        )
        # Exactly one dispatch despite 200 ticks. The dispatched
        # universe carries the QuietWash wash anchors.
        assert len(disp.sends) == 1
        sent = disp.sends[0]
        assert sent[0] == 255    # ch 1 master
        assert sent[10] == 100   # ch 11 wash A R
        assert sent[11] == 100   # ch 12 wash A G
        assert sent[12] == 100   # ch 13 wash A B

    def test_changing_universe_dispatches_each_change(self, tmp_path):
        # SparkleOnBeat toggles the pulse-trigger channel between
        # 255 (on-beat tick) and 0 (re-arm tick), so the universe
        # changes twice per beat. Each change must produce one
        # dispatch even though most ticks are still suppressed.
        (tmp_path / "_default.cues").write_text(
            "@bpm 120\n"
            "@default_fx sparkle_on_beat 255 0 0 100\n"
        )
        backend = FakeBackend([
            NowPlaying(True, "A", "B", position_ms=0, duration_ms=10_000),
        ])
        disp = CaptureDispatcher()
        clock = [0]
        def now_ms():
            return clock[0]
        def sleep(seconds):
            clock[0] += max(int(seconds * 1000), 20)
        run(
            nowplaying_backend=backend, dispatcher=disp,
            songs_dir=str(tmp_path), default_bpm=120,
            log=lambda *a, **kw: None,
            sleep=sleep, now_ms=now_ms,
            iteration_budget=100,  # ~2 s of wall-clock at 20 ms ticks
        )
        # At 120 BPM = 2 beats/s, over 2 seconds that's 4 beats
        # = roughly 8 dispatches (rising + falling per beat) plus the
        # initial dispatch. Should be far fewer than the 100 ticks
        # but more than 1.
        n = len(disp.sends)
        assert 4 <= n <= 20, "expected ~8 dispatches, got %d" % n

    def test_debug_output_uses_slug_for_artist_title(self, tmp_path):
        # The LD reads the orchestrator's log to know what file name
        # the matcher is hunting for. Showing slug form (the same
        # transformation the matcher applies) trains the LD on the
        # expected file naming convention.
        (tmp_path / "_default.cues").write_text(
            "@default_fx quiet_wash 100 0 0\n"
        )
        backend = FakeBackend([
            NowPlaying(True, "Coldplay", "A Sky Full of Stars",
                       position_ms=0, duration_ms=295_000),
        ])
        disp = CaptureDispatcher()
        lines = []
        clock = [0]
        def now_ms():
            return clock[0]
        def sleep(seconds):
            clock[0] += max(int(seconds * 1000), 20)
        run(
            nowplaying_backend=backend, dispatcher=disp,
            songs_dir=str(tmp_path), default_bpm=120,
            log=lambda msg: lines.append(msg),
            debug=True,
            sleep=sleep, now_ms=now_ms,
            iteration_budget=80,  # enough to cross the 1 s poll boundary
        )
        joined = "\n".join(lines)
        assert "coldplay-a-sky-full-of-stars" in joined
        # The pre-slug form must NOT appear (we want a single visual
        # convention so the LD doesn't see two formats).
        assert "Coldplay / A Sky Full of Stars" not in joined
        assert "'Coldplay', 'A Sky Full of Stars'" not in joined

    def test_inactive_to_active_clears_dispatch_cache(self, tmp_path):
        # If the orchestrator stops sending (no FX) and later
        # re-activates with the same universe bytes, the first frame
        # after re-activation must still be dispatched so the StickC
        # doesn't sit on stale state from before the quiet period.
        (tmp_path / "_default.cues").write_text(
            "@default_fx quiet_wash 100 100 100\n"
        )
        backend = FakeBackend([
            # First track loads default_fx, sends one wash frame.
            NowPlaying(True, "A", "B", position_ms=0, duration_ms=10_000),
            # No source - runner goes idle, scheduler stops, no FX.
            None,
            None,
            # Same track again - default_fx reloads, fresh send.
            NowPlaying(True, "A", "B", position_ms=0, duration_ms=10_000),
        ])
        disp = CaptureDispatcher()
        clock = [0]
        def now_ms():
            return clock[0]
        # Force enough ticks per poll-cycle that all 4 polls fire.
        def sleep(seconds):
            clock[0] += max(int(seconds * 1000), 500)
        run(
            nowplaying_backend=backend, dispatcher=disp,
            songs_dir=str(tmp_path), default_bpm=120,
            log=lambda *a, **kw: None,
            sleep=sleep, now_ms=now_ms,
            iteration_budget=30,
        )
        # Two distinct active periods -> at least two dispatches,
        # even though both carry byte-identical wash universes.
        assert len(disp.sends) >= 2
        assert disp.sends[0] == disp.sends[-1]   # same bytes both times

    def test_loop_loads_cue_file_and_dispatches_universe(self, tmp_path):
        # Default FX establishes the bed; a later cue switches to a
        # different FX so we can prove the scheduler actually fires
        # cues (not just the default).
        (tmp_path / "coldplay-fix-you.cues").write_text(
            "@bpm 138\n"
            "@default_fx quiet_wash 100 0 0\n"
            "00:05 drift_wash 50 100 200 220 80\n"
        )
        backend = FakeBackend([
            NowPlaying(True, "Coldplay", "Fix You",
                       position_ms=0, duration_ms=295_000),
            NowPlaying(True, "Coldplay", "Fix You",
                       position_ms=5_500, duration_ms=295_000),
        ])
        disp = CaptureDispatcher()
        clock = [0]
        def now_ms():
            return clock[0]
        def sleep(seconds):
            clock[0] += max(int(seconds * 1000), 20)
        run(
            nowplaying_backend=backend,
            dispatcher=disp,
            songs_dir=str(tmp_path),
            default_bpm=120,
            log=lambda *a, **kw: None,
            sleep=sleep,
            now_ms=now_ms,
            iteration_budget=200,
        )
        # By the end, the 00:05 drift_wash cue should be running -
        # Wash A=(50,100,0,...) Wash B=(200,220,0,...) cycle=80.
        last = disp.sends[-1]
        assert last[0] == 255            # ch 1 master (drift_wash sets to 255)
        assert last[10] == 50            # ch 11 Wash A R
        assert last[11] == 100           # ch 12 Wash A G
        assert last[13] == 200           # ch 14 Wash B R
        assert last[14] == 220           # ch 15 Wash B G
        assert last[16] == 80            # ch 17 cycle
