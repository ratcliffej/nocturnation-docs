"""Linux now-playing backend via MPRIS (D-Bus).

Reads ``org.mpris.MediaPlayer2.Player`` properties from whichever
session-bus media player is currently active (Spotify, Rhythmbox,
VLC, browser tabs via the MPRIS bridge, etc.).

Install (Linux only)::

    sudo apt install python3-gi gir1.2-glib-2.0   # GObject runtime
    pip install pydbus

The backend lazy-imports ``pydbus`` only when polled, so the
orchestrator package stays importable on macOS / Windows.

When multiple players are active, prefer the one currently
``PlaybackStatus == "Playing"``; fall back to the first ``Paused``
otherwise. Stopped players are ignored.

MPRIS units:
- ``Metadata['mpris:length']`` is in **microseconds**. /= 1000 -> ms.
- ``Position`` is also in microseconds. /= 1000 -> ms.
- ``Metadata['xesam:artist']`` is a list of strings; join with ", "
  to match the matcher's expectations for the slug step.
"""

from .base import NowPlaying, NowPlayingBackend, NowPlayingError


_MPRIS_NAME_PREFIX = "org.mpris.MediaPlayer2."


def _us_to_ms(value):
    try:
        return max(0, int(value) // 1000)
    except (ValueError, TypeError):
        return 0


def _snapshot_from_mpris(player_data):
    """Pure conversion: dict-shaped MPRIS data -> NowPlaying.

    ``player_data`` shape (None when no active player)::

        {
            'status':     'Playing' | 'Paused' | 'Stopped',
            'metadata':   {
                'xesam:title':  '...',
                'xesam:artist': ['...', '...'],
                'mpris:length': 295000000,
            },
            'position_us': 30500000,
        }
    """
    if player_data is None:
        return None
    metadata = player_data.get("metadata") or {}
    title = (metadata.get("xesam:title") or "").strip()
    artists = metadata.get("xesam:artist") or []
    if isinstance(artists, str):
        artists = [artists]
    artist = ", ".join(a for a in artists if a).strip()
    genres = metadata.get("xesam:genre") or []
    if isinstance(genres, str):
        genres = [genres]
    genre = ", ".join(g for g in genres if g).strip()
    if not title and not artist:
        return None
    return NowPlaying(
        is_playing=player_data.get("status") == "Playing",
        artist=artist,
        title=title,
        position_ms=_us_to_ms(player_data.get("position_us", 0)),
        duration_ms=_us_to_ms(metadata.get("mpris:length", 0)),
        genre=genre,
    )


def _default_session_data_provider():
    """Default OS-side path: query the session bus and pick a player."""
    try:
        from pydbus import SessionBus
    except ImportError as exc:
        raise NowPlayingError(
            "pydbus is not installed; run `pip install pydbus` to enable "
            "Linux now-playing support"
        ) from exc

    bus = SessionBus()
    dbus = bus.get(".DBus")
    names = [n for n in dbus.ListNames() if n.startswith(_MPRIS_NAME_PREFIX)]
    if not names:
        return None

    playing = None
    paused = None
    for name in names:
        try:
            player = bus.get(name, "/org/mpris/MediaPlayer2")
        except Exception:
            continue
        try:
            status = str(player.PlaybackStatus)
            metadata = dict(player.Metadata)
            # Position may be missing on some players (e.g. browser tabs).
            position_us = 0
            try:
                position_us = int(player.Position)
            except Exception:
                pass
        except Exception:
            continue
        data = {
            "status": status,
            "metadata": metadata,
            "position_us": position_us,
        }
        if status == "Playing":
            playing = data
            break
        if status == "Paused" and paused is None:
            paused = data
    return playing or paused


class LinuxBackend(NowPlayingBackend):
    """MPRIS polling backend.

    Args:
        provider (callable | None): callable returning the
            player_data dict (or None). Defaults to the real pydbus
            path; tests can inject a fake.
    """

    def __init__(self, provider=None):
        self._provider = provider or _default_session_data_provider

    def ensure_available(self):
        if self._provider is _default_session_data_provider:
            try:
                import pydbus  # noqa: F401
            except ImportError as exc:
                raise NowPlayingError(
                    "pydbus is not installed; run `pip install pydbus` (and "
                    "the system GObject runtime, e.g. python3-gi) to enable "
                    "Linux now-playing support"
                ) from exc

    def poll(self):
        try:
            data = self._provider()
        except NowPlayingError:
            raise
        except Exception as exc:
            raise NowPlayingError("MPRIS query failed: %s" % exc) from exc
        return _snapshot_from_mpris(data)
