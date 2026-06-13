"""macOS now-playing backend via the `nowplaying-cli` tool.

`brew install nowplaying-cli` makes the binary available; it wraps
the private MediaRemote framework and prints the requested fields
one per line.

Polled invocation::

    nowplaying-cli get title artist elapsedTime duration playbackRate

Output is one value per line, blank line for missing fields. We
interpret playbackRate > 0 as "playing" (paused tracks report 0).
"""

import shutil
import subprocess

from .base import NowPlaying, NowPlayingBackend, NowPlayingError


_FIELDS = ("title", "artist", "elapsedTime", "duration", "playbackRate")


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


def _default_runner(args, timeout):
    completed = subprocess.run(
        args,
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return completed.stdout, completed.returncode


def _parse_output(stdout):
    """Parse nowplaying-cli's line-per-field output.

    Empty / 'null' values denote "no source"; returning None lets the
    main loop go ambient.
    """
    lines = stdout.splitlines()
    # Pad to expected field count so we don't IndexError on truncated
    # output from older nowplaying-cli builds.
    while len(lines) < len(_FIELDS):
        lines.append("")
    title    = lines[0].strip()
    artist   = lines[1].strip()
    elapsed  = lines[2].strip()
    duration = lines[3].strip()
    rate     = lines[4].strip()

    # No source: nowplaying-cli prints 'null' for every field.
    if not title and not artist:
        return None
    if title.lower() == "null" and artist.lower() == "null":
        return None

    position_ms = _seconds_to_ms(elapsed)
    duration_ms = _seconds_to_ms(duration)
    is_playing = _float_or_zero(rate) > 0.0

    return NowPlaying(
        is_playing=is_playing,
        artist=artist,
        title=title,
        position_ms=position_ms,
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
