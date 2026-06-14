"""macOS now-playing backend via the `nowplaying-cli` tool.

`brew install nowplaying-cli` makes the binary available; it wraps
the private MediaRemote framework and prints the requested fields
one per line.

Polled invocation::

    nowplaying-cli get title artist elapsedTime duration playbackRate \\
                       infoUpdateTime

Output is one value per line, blank line for missing fields. We
interpret playbackRate > 0 as "playing" (paused tracks report 0).

The elapsedTime returned by MediaRemote is the cached value at the
last state change (play / pause / seek); it does NOT tick forward
while a track plays. To get the live position we also read
`infoUpdateTime` (NSDate seconds since 2001-01-01) and extrapolate:

    live_position_ms = elapsedTime_ms
                       + (now_unix - (infoUpdateTime + NSDATE_OFFSET))
                         * 1000 * playbackRate

Without this, a playing track's position would stay frozen at
whatever it was when the user pressed play, and the cue scheduler
would never advance past time zero.
"""

import shutil
import subprocess
import time

from .base import NowPlaying, NowPlayingBackend, NowPlayingError


_FIELDS = (
    "title", "artist", "elapsedTime", "duration", "playbackRate",
    "infoUpdateTime",
)

# NSDate reference date (2001-01-01 00:00:00 UTC) as a Unix timestamp.
_NSDATE_TO_UNIX_OFFSET = 978_307_200

# Sanity cap: ignore extrapolation gaps larger than this. Protects
# against a stale infoUpdateTime from a previous Music.app session
# pushing the position into orbit.
_MAX_EXTRAPOLATION_S = 7200  # 2 hours


class MacOSBackend(NowPlayingBackend):
    """nowplaying-cli polling backend.

    Args:
        binary (str): name or path of the nowplaying-cli executable.
            Default 'nowplaying-cli' (resolved via PATH).
        runner (callable): subprocess runner accepting (args_list,
            timeout) and returning (stdout, returncode). Injectable
            for tests; defaults to a thin subprocess.run wrapper.
        timeout (float): seconds before a poll is treated as failed.
    """

    def __init__(self, binary="nowplaying-cli", runner=None, timeout=2.0):
        self.binary = binary
        self._runner = runner or _default_runner
        self.timeout = timeout

    def ensure_available(self):
        """Raise NowPlayingError if the binary can't be found. Call
        this once at startup; cheap, no IPC."""
        # If a custom runner is injected (tests), skip the binary check.
        if self._runner is _default_runner and shutil.which(self.binary) is None:
            raise NowPlayingError(
                "%s not found on PATH; install with `brew install nowplaying-cli`"
                % self.binary
            )

    def poll(self):
        try:
            stdout, rc = self._runner(
                [self.binary, "get", *_FIELDS], self.timeout,
            )
        except FileNotFoundError:
            raise NowPlayingError(
                "%s missing; install with `brew install nowplaying-cli`"
                % self.binary
            )
        if rc != 0:
            raise NowPlayingError(
                "%s exited %d" % (self.binary, rc)
            )
        return _parse_output(stdout)


def _now_unix():
    return time.time()


def _default_runner(args, timeout):
    completed = subprocess.run(
        args,
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return completed.stdout, completed.returncode


def _parse_output(stdout, *, now_unix=None):
    """Parse nowplaying-cli's line-per-field output.

    Empty / 'null' values denote "no source"; returning None lets the
    main loop go ambient.

    Args:
        stdout (str): raw nowplaying-cli stdout.
        now_unix (callable | None): provider of the current Unix
            timestamp; defaults to time.time. Injected by tests so
            the infoUpdateTime extrapolation is deterministic.
    """
    if now_unix is None:
        now_unix = _now_unix
    lines = stdout.splitlines()
    # Pad to expected field count so we don't IndexError on truncated
    # output from older nowplaying-cli builds.
    while len(lines) < len(_FIELDS):
        lines.append("")
    title       = lines[0].strip()
    artist      = lines[1].strip()
    elapsed     = lines[2].strip()
    duration    = lines[3].strip()
    rate        = lines[4].strip()
    info_update = lines[5].strip()

    # No source: nowplaying-cli prints 'null' for every field.
    if not title and not artist:
        return None
    if title.lower() == "null" and artist.lower() == "null":
        return None

    position_ms = _seconds_to_ms(elapsed)
    duration_ms = _seconds_to_ms(duration)
    playback_rate = _float_or_zero(rate)
    is_playing = playback_rate > 0.0

    # Extrapolate the live position from the OS sample. MediaRemote
    # only refreshes elapsedTime on state changes, so without this
    # the position stays frozen across an entire track.
    info_update_nsdate = _float_or_zero(info_update)
    if is_playing and info_update_nsdate > 0:
        unix_info_update = info_update_nsdate + _NSDATE_TO_UNIX_OFFSET
        delta_s = now_unix() - unix_info_update
        if 0 < delta_s <= _MAX_EXTRAPOLATION_S:
            position_ms += int(delta_s * 1000 * playback_rate)

    return NowPlaying(
        is_playing=is_playing,
        artist=artist,
        title=title,
        position_ms=max(0, position_ms),
        duration_ms=duration_ms,
    )


def _seconds_to_ms(token):
    try:
        return int(round(float(token) * 1000))
    except (ValueError, TypeError):
        return 0


def _float_or_zero(token):
    try:
        return float(token)
    except (ValueError, TypeError):
        return 0.0
