"""macOS now-playing backend via the `nowplaying-cli` tool.

`brew install nowplaying-cli` makes the binary available; it wraps
the private MediaRemote framework.

Polled invocation::

    nowplaying-cli get-raw

This returns the full MediaRemote info dictionary as JSON. We
deliberately avoid `nowplaying-cli get <field...>`: at least on the
0.x builds shipping via Homebrew today it returns 0 for
`elapsedTime` regardless of the real value, while the same field in
`get-raw` reports the actual position. The bug is in the per-field
plumbing, not the underlying API, so `get-raw` is the workaround.

Output (real example, Coldplay / A Sky Full of Stars at 52.97 s)::

    {
      "kMRMediaRemoteNowPlayingInfoTitle" : "A Sky Full of Stars",
      "kMRMediaRemoteNowPlayingInfoArtist" : "Coldplay",
      "kMRMediaRemoteNowPlayingInfoElapsedTime" : 52.966817133,
      "kMRMediaRemoteNowPlayingInfoDuration" : 268,
      "kMRMediaRemoteNowPlayingInfoPlaybackRate" : 0,
      ...
    }

`playbackRate > 0` means "playing"; 0 means paused / stopped. The
MediaRemote `Timestamp` field that would let us extrapolate the live
position purely from one poll isn't present on this build, so we
rely on the scheduler's PositionTracker (wall-clock interpolation
between polls, anchored only on real changes) for the live position.
"""

import json
import shutil
import subprocess

from .base import NowPlaying, NowPlayingBackend, NowPlayingError


# MediaRemote info-dict keys we read.
_KEY_TITLE         = "kMRMediaRemoteNowPlayingInfoTitle"
_KEY_ARTIST        = "kMRMediaRemoteNowPlayingInfoArtist"
_KEY_ELAPSED_TIME  = "kMRMediaRemoteNowPlayingInfoElapsedTime"
_KEY_DURATION      = "kMRMediaRemoteNowPlayingInfoDuration"
_KEY_PLAYBACK_RATE = "kMRMediaRemoteNowPlayingInfoPlaybackRate"


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
                [self.binary, "get-raw"], self.timeout,
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
        return _parse_raw_output(stdout)


def _default_runner(args, timeout):
    completed = subprocess.run(
        args,
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return completed.stdout, completed.returncode


def _parse_raw_output(stdout):
    """Parse `nowplaying-cli get-raw` JSON output into a NowPlaying.

    Returns None when no audio source is registered with MediaRemote
    (empty / non-object response, or no Title/Artist).
    """
    stdout = stdout.strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    title  = _str_or_empty(data.get(_KEY_TITLE))
    artist = _str_or_empty(data.get(_KEY_ARTIST))
    if not title and not artist:
        return None

    position_ms   = _seconds_to_ms(data.get(_KEY_ELAPSED_TIME))
    duration_ms   = _seconds_to_ms(data.get(_KEY_DURATION))
    playback_rate = _float_or_zero(data.get(_KEY_PLAYBACK_RATE))
    is_playing    = playback_rate > 0.0

    return NowPlaying(
        is_playing=is_playing,
        artist=artist,
        title=title,
        position_ms=max(0, position_ms),
        duration_ms=duration_ms,
    )


def _str_or_empty(value):
    if value is None:
        return ""
    return str(value).strip()


def _seconds_to_ms(value):
    try:
        return int(round(float(value) * 1000))
    except (ValueError, TypeError):
        return 0


def _float_or_zero(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
