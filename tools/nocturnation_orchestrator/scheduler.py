"""Cue scheduler + position tracker.

The orchestrator main loop polls the now-playing backend ~1 Hz but
ticks the FX engine ~50 Hz. Between polls the tracker interpolates
position by wall-clock; the scheduler uses the interpolated position
to fire any cues that have come due since the previous tick.

Seek handling:
- Backward jump (e.g. user dragged the scrubber back): reset cursor
  to 0 and re-fire any cues that fall at or before the new position.
  The orchestrator runs the LAST such cue (so a mid-song restart
  picks up the current ambient FX without flashing through earlier
  ones).
- Forward jump greater than SEEK_FORWARD_THRESHOLD_MS: advance cursor
  past skipped cues silently. Fire any cue that lands exactly at the
  new position. Same "land on most recent" rule applies.
- Normal monotonic advance: fire every cue whose time_ms is <=
  current position.

Pause / resume is tracker-only; cues do not fire while paused.
Song change resets everything via `set_cue_file(new_file)`.
"""

import time as _time


SEEK_FORWARD_THRESHOLD_MS = 2_000   # forward jump over this counts as a seek
PAUSE_DRIFT_THRESHOLD_MS  =   500   # tolerance for "position hasn't moved"


def _now_ms_default():
    return int(_time.monotonic() * 1000)


class PositionTracker:
    """Estimates song position between now-playing polls.

    On each poll, calls `update_from_poll(np, now_ms)` with the
    NowPlaying snapshot. Between polls, `current_position(now_ms)`
    extrapolates by wall clock if the track is playing.
    """

    __slots__ = (
        "_anchor_position_ms",
        "_anchor_wall_ms",
        "_is_playing",
        "_artist",
        "_title",
    )

    def __init__(self):
        self._anchor_position_ms = 0
        self._anchor_wall_ms = 0
        self._is_playing = False
        self._artist = ""
        self._title = ""

    def update_from_poll(self, np, now_ms):
        """Absorb a new NowPlaying snapshot."""
        self._anchor_position_ms = np.position_ms
        self._anchor_wall_ms = now_ms
        self._is_playing = np.is_playing
        self._artist = np.artist
        self._title = np.title

    def clear(self):
        """No source is playing."""
        self._is_playing = False
        self._artist = ""
        self._title = ""

    def current_position(self, now_ms):
        if not self._is_playing:
            return self._anchor_position_ms
        elapsed = now_ms - self._anchor_wall_ms
        if elapsed < 0:
            elapsed = 0
        return self._anchor_position_ms + elapsed

    @property
    def is_playing(self):
        return self._is_playing

    @property
    def track_key(self):
        return (self._artist, self._title)


class CueScheduler:
    """Walks a CueFile against a position cursor and fires cues.

    Usage from the main loop::

        scheduler.set_cue_file(cue_file, now_ms=now)
        # each tick:
        scheduler.advance(position_ms, now_ms=now)
    """

    __slots__ = (
        "runner",
        "cue_file",
        "_cursor",
        "_last_position_ms",
    )

    def __init__(self, runner):
        self.runner = runner
        self.cue_file = None
        self._cursor = 0
        self._last_position_ms = -1

    def set_cue_file(self, cue_file, now_ms):
        """Switch to a new cue file. Cancels any running FX and
        applies the file-level default FX (if any)."""
        self.cue_file = cue_file
        self._cursor = 0
        self._last_position_ms = -1
        self.runner.cancel(now_ms=now_ms)
        if cue_file is None:
            return
        if cue_file.default_fx_id:
            self.runner.start(
                cue_file.default_fx_id,
                bpm=cue_file.default_bpm,
                buildup_s=0,
                params=cue_file.default_fx_params,
                position_ms=0,
                now_ms=now_ms,
            )

    def stop(self, now_ms):
        """Tear down. Cancels any running FX and clears state."""
        self.set_cue_file(None, now_ms=now_ms)

    def advance(self, position_ms, now_ms):
        """Fire any cues that fall at or before ``position_ms``.

        Handles backward / forward seeks: see module docstring.
        """
        if self.cue_file is None or not self.cue_file.cues:
            self._last_position_ms = position_ms
            return

        seek_back = (
            self._last_position_ms >= 0
            and position_ms + PAUSE_DRIFT_THRESHOLD_MS < self._last_position_ms
        )
        seek_forward = (
            self._last_position_ms >= 0
            and position_ms - self._last_position_ms > SEEK_FORWARD_THRESHOLD_MS
        )

        if seek_back:
            # Re-walk from the top.
            self._cursor = 0
        if seek_back or seek_forward:
            # Fire only the most recent cue at-or-before the new position.
            target = None
            while (self._cursor < len(self.cue_file.cues)
                   and self.cue_file.cues[self._cursor].time_ms <= position_ms):
                target = self.cue_file.cues[self._cursor]
                self._cursor += 1
            if target is not None:
                self._fire(target, position_ms, now_ms)
        else:
            # Normal monotonic advance: fire every cue in window.
            while (self._cursor < len(self.cue_file.cues)
                   and self.cue_file.cues[self._cursor].time_ms <= position_ms):
                cue = self.cue_file.cues[self._cursor]
                self._cursor += 1
                self._fire(cue, position_ms, now_ms)

        self._last_position_ms = position_ms

    def _fire(self, cue, position_ms, now_ms):
        effective_bpm = cue.bpm or self.cue_file.default_bpm
        # position offset into the FX timeline = where we ARE relative
        # to the cue's start. Non-zero on seek / late join.
        cue_position_ms = position_ms - cue.time_ms
        if cue_position_ms < 0:
            cue_position_ms = 0
        self.runner.start(
            cue.fx_id,
            bpm=effective_bpm,
            buildup_s=cue.buildup_s,
            params=cue.params,
            position_ms=cue_position_ms,
            now_ms=now_ms,
        )
