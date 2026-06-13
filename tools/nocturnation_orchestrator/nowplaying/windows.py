"""Windows now-playing backend via SMTC.

Uses the System Media Transport Controls API
(``Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager``)
to read whichever app currently owns the OS media surface (Spotify,
Apple Music for Windows, browser tabs that support Media Session,
Groove, VLC with the SMTC plugin, etc.).

Install (Windows only)::

    pip install winsdk

The backend lazy-imports ``winsdk`` only when the OS surface is
actually polled, so the orchestrator package stays importable on
macOS / Linux.

Conversions:
- SMTC ``Position`` is a ``timedelta``. Convert to ms via
  ``total_seconds() * 1000`` to handle the .NET TimeSpan tick width
  (100 ns) translation winsdk has already done for us.
- ``PlaybackStatus == 4`` means "Playing"; anything else (Paused /
  Stopped / Changing / Opened / Closed) we treat as not-playing.
"""

import asyncio

from .base import NowPlaying, NowPlayingBackend, NowPlayingError


# SMTC PlaybackStatus enum values (Windows.Media.MediaPlaybackStatus).
_PLAYBACK_PLAYING = 4


async def _default_async_snapshot():
    """Default OS-side path: query SMTC for the current session.

    Returns a dict suitable for ``_snapshot_from_smtc_data`` or None
    if there's no current session.
    """
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Manager,
        )
    except ImportError as exc:
        raise NowPlayingError(
            "winsdk is not installed; run `pip install winsdk` to enable "
            "Windows now-playing support"
        ) from exc

    manager = await Manager.request_async()
    session = manager.get_current_session()
    if session is None:
        return None

    props = await session.try_get_media_properties_async()
    timeline = session.get_timeline_properties()
    playback = session.get_playback_info()

    return {
        "title":            props.title or "",
        "artist":           props.artist or "",
        "position_ms":      int(timeline.position.total_seconds() * 1000),
        "duration_ms":      int(timeline.end_time.total_seconds() * 1000),
        "playback_status":  int(playback.playback_status),
    }


def _snapshot_from_smtc_data(data):
    """Pure conversion: dict from the OS path to a NowPlaying.

    ``data`` is the dict returned by `_default_async_snapshot` (or by a
    fake in tests). None means no current session.
    """
    if data is None:
        return None
    title = (data.get("title") or "").strip()
    artist = (data.get("artist") or "").strip()
    if not title and not artist:
        return None
    return NowPlaying(
        is_playing=data.get("playback_status") == _PLAYBACK_PLAYING,
        artist=artist,
        title=title,
        position_ms=max(0, int(data.get("position_ms", 0))),
        duration_ms=max(0, int(data.get("duration_ms", 0))),
    )


class WindowsBackend(NowPlayingBackend):
    """SMTC polling backend.

    Args:
        async_snapshot (callable | None): async callable returning a
            dict (or None) shaped like ``_default_async_snapshot``.
            Injectable for tests; defaults to the real winsdk path.
    """

    def __init__(self, async_snapshot=None):
        self._async_snapshot = async_snapshot or _default_async_snapshot

    def ensure_available(self):
        """Raise NowPlayingError if winsdk isn't importable. Called
        once at startup so a missing dep fails loudly."""
        if self._async_snapshot is _default_async_snapshot:
            try:
                import winsdk  # noqa: F401
            except ImportError as exc:
                raise NowPlayingError(
                    "winsdk is not installed; run `pip install winsdk` to "
                    "enable Windows now-playing support"
                ) from exc

    def poll(self):
        try:
            data = asyncio.run(self._async_snapshot())
        except NowPlayingError:
            raise
        except Exception as exc:
            raise NowPlayingError("SMTC query failed: %s" % exc) from exc
        return _snapshot_from_smtc_data(data)
