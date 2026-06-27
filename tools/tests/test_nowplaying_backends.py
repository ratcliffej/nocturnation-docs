"""Cross-platform now-playing backend conversion tests (B6).

Verifies the snapshot-conversion layer of each backend without
calling into real OS APIs. The real-API path is bench-validated.

The backends use dependency injection (subprocess runner for macOS,
async snapshot callable for Windows, provider for Linux) so the
tests can inject fakes that produce known input shapes.
"""

import asyncio

import pytest

from nocturnation_orchestrator.nowplaying.linux import (
    LinuxBackend, _snapshot_from_mpris, _us_to_ms,
)
from nocturnation_orchestrator.nowplaying.windows import (
    WindowsBackend, _snapshot_from_smtc_data,
)


# ---------------------------------------------------------------------------
# Windows / SMTC
# ---------------------------------------------------------------------------

class TestWindowsSnapshotConversion:
    def test_playing_track(self):
        np = _snapshot_from_smtc_data({
            "title": "Fix You",
            "artist": "Coldplay",
            "position_ms": 30_500,
            "duration_ms": 295_000,
            "playback_status": 4,    # Playing
        })
        assert np is not None
        assert np.is_playing
        assert np.title == "Fix You"
        assert np.artist == "Coldplay"
        assert np.position_ms == 30_500
        assert np.duration_ms == 295_000

    def test_paused_track(self):
        np = _snapshot_from_smtc_data({
            "title": "Fix You",
            "artist": "Coldplay",
            "position_ms": 30_500,
            "duration_ms": 295_000,
            "playback_status": 5,    # Paused
        })
        assert np is not None
        assert np.is_playing is False

    def test_none_source(self):
        assert _snapshot_from_smtc_data(None) is None

    def test_empty_title_and_artist_is_none(self):
        np = _snapshot_from_smtc_data({
            "title": "", "artist": "",
            "position_ms": 0, "duration_ms": 0, "playback_status": 4,
        })
        assert np is None

    def test_only_title_still_returns_snapshot(self):
        np = _snapshot_from_smtc_data({
            "title": "Untitled stream", "artist": "",
            "position_ms": 0, "duration_ms": 0, "playback_status": 4,
        })
        assert np is not None
        assert np.title == "Untitled stream"

    def test_negative_positions_clamped(self):
        np = _snapshot_from_smtc_data({
            "title": "T", "artist": "A",
            "position_ms": -50, "duration_ms": -10, "playback_status": 4,
        })
        assert np.position_ms == 0
        assert np.duration_ms == 0

    def test_genre_passes_through(self):
        np = _snapshot_from_smtc_data({
            "title": "T", "artist": "A",
            "position_ms": 0, "duration_ms": 0,
            "playback_status": 4, "genre": "Metal",
        })
        assert np.genre == "Metal"

    def test_missing_genre_is_empty(self):
        np = _snapshot_from_smtc_data({
            "title": "T", "artist": "A",
            "position_ms": 0, "duration_ms": 0,
            "playback_status": 4,
        })
        assert np.genre == ""


class TestWindowsBackendInjection:
    def test_poll_runs_injected_async(self):
        async def fake_snapshot():
            return {
                "title": "Fix You",
                "artist": "Coldplay",
                "position_ms": 1_500,
                "duration_ms": 295_000,
                "playback_status": 4,
            }
        backend = WindowsBackend(async_snapshot=fake_snapshot)
        np = backend.poll()
        assert np is not None
        assert np.title == "Fix You"
        assert np.is_playing

    def test_poll_returns_none_when_provider_yields_none(self):
        async def fake_snapshot():
            return None
        backend = WindowsBackend(async_snapshot=fake_snapshot)
        assert backend.poll() is None


# ---------------------------------------------------------------------------
# Linux / MPRIS
# ---------------------------------------------------------------------------

class TestUsToMs:
    def test_basic(self):
        assert _us_to_ms(30_500_000) == 30_500

    def test_string_input(self):
        # MPRIS often hands typed values that already int-cast.
        assert _us_to_ms("1500000") == 1_500

    def test_garbage_input_is_zero(self):
        assert _us_to_ms("nope") == 0
        assert _us_to_ms(None) == 0


class TestLinuxSnapshotConversion:
    def test_playing_track(self):
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {
                "xesam:title": "Fix You",
                "xesam:artist": ["Coldplay"],
                "mpris:length": 295_000_000,    # microseconds
            },
            "position_us": 30_500_000,
        })
        assert np is not None
        assert np.is_playing
        assert np.title == "Fix You"
        assert np.artist == "Coldplay"
        assert np.position_ms == 30_500
        assert np.duration_ms == 295_000

    def test_paused_track(self):
        np = _snapshot_from_mpris({
            "status": "Paused",
            "metadata": {
                "xesam:title": "T",
                "xesam:artist": ["A"],
                "mpris:length": 0,
            },
            "position_us": 1_000_000,
        })
        assert np is not None
        assert np.is_playing is False
        assert np.position_ms == 1_000

    def test_multiple_artists_joined(self):
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {
                "xesam:title": "Track",
                "xesam:artist": ["Artist A", "Artist B"],
                "mpris:length": 0,
            },
            "position_us": 0,
        })
        assert np.artist == "Artist A, Artist B"

    def test_artist_string_not_list(self):
        # Some MPRIS providers return a single string instead of a list.
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {
                "xesam:title": "Track",
                "xesam:artist": "Solo",
                "mpris:length": 0,
            },
            "position_us": 0,
        })
        assert np.artist == "Solo"

    def test_none_player(self):
        assert _snapshot_from_mpris(None) is None

    def test_empty_metadata_is_none(self):
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {},
            "position_us": 0,
        })
        assert np is None

    def test_genre_list_joined(self):
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {
                "xesam:title": "Track",
                "xesam:artist": ["Artist"],
                "xesam:genre": ["Alternative", "Indie"],
                "mpris:length": 0,
            },
            "position_us": 0,
        })
        assert np.genre == "Alternative, Indie"

    def test_genre_string_passed_through(self):
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {
                "xesam:title": "Track",
                "xesam:artist": ["Artist"],
                "xesam:genre": "Metal",
                "mpris:length": 0,
            },
            "position_us": 0,
        })
        assert np.genre == "Metal"

    def test_missing_genre_is_empty(self):
        np = _snapshot_from_mpris({
            "status": "Playing",
            "metadata": {
                "xesam:title": "Track",
                "xesam:artist": ["Artist"],
                "mpris:length": 0,
            },
            "position_us": 0,
        })
        assert np.genre == ""


class TestLinuxBackendInjection:
    def test_poll_uses_injected_provider(self):
        def fake_provider():
            return {
                "status": "Playing",
                "metadata": {
                    "xesam:title": "Fix You",
                    "xesam:artist": ["Coldplay"],
                    "mpris:length": 295_000_000,
                },
                "position_us": 0,
            }
        backend = LinuxBackend(provider=fake_provider)
        np = backend.poll()
        assert np is not None
        assert np.title == "Fix You"

    def test_poll_returns_none_when_provider_yields_none(self):
        backend = LinuxBackend(provider=lambda: None)
        assert backend.poll() is None
