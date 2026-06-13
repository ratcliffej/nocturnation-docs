"""Now-playing backend interface."""

from dataclasses import dataclass


class NowPlayingError(Exception):
    """Raised when a backend can't talk to the OS (binary missing,
    permission denied, parse failure)."""


@dataclass(frozen=True)
class NowPlaying:
    """Snapshot of what an OS audio player is playing right now.

    Attributes:
        is_playing (bool): True if playback is currently active. False
            for paused / stopped / no source.
        artist (str): artist label. Free text; the matcher slugifies.
        title (str): track title. Free text.
        position_ms (int): cursor position into the track, in ms.
        duration_ms (int): total track length, in ms. 0 if unknown.
    """
    is_playing: bool
    artist: str
    title: str
    position_ms: int
    duration_ms: int


class NowPlayingBackend:
    """Polled source of NowPlaying snapshots.

    Returns None when no audio is currently being served by the OS
    (no source registered with the now-playing system). The main
    loop interprets None as "go ambient / no song".
    """

    def poll(self):  # -> NowPlaying | None
        """Return the current snapshot, or None if no source is active.

        Implementations may raise NowPlayingError on hard failures
        (e.g. backing binary is missing); the main loop catches and
        treats as "no source" plus logs.
        """
        raise NotImplementedError
