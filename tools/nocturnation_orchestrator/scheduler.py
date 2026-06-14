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

    Stale-cache handling: macOS MediaRemote caches elapsedTime at the
    last play / pause / seek; some music apps never push fresh values
    after that, so polling returns the same number indefinitely. To
    avoid resetting the wall-clock anchor on every poll (which would
    pin the position to whatever cached value the OS keeps returning),
    we only re-anchor when the OS-reported position has actually
    changed, when the play state flips, or when the track changes.
    Otherwise the wall-clock interpolation just keeps advancing from
    the previous anchor.
    """

    __slots__ = (
        "_anchor_position_ms",
        "_anchor_wall_ms",
        "_is_playing",
        "_artist",
        "_title",
        "_last_os_position_ms",
        "_has_anchor",
    )

    def __init__(self):
        self._anchor_position_ms = 0
        self._anchor_wall_ms = 0
        self._is_playing = False
        self._artist = ""
        self._title = ""
        self._last_os_position_ms = -1
        self._has_anchor = False

    def update_from_poll(self, np, now_ms):
        """Absorb a new NowPlaying snapshot.

        Re-anchors only on genuine events (track change, play / pause,
        OS-reported position actually moved). When the OS returns the
        same position as last poll while still claiming to play, we
        treat that as a stale cache and let wall-clock interpolation
        from the previous anchor continue.
        """
        track_changed = (
            np.artist != self._artist or np.title != self._title
        )
        state_changed = (np.is_playing != self._is_playing)
        position_changed = (np.position_ms != self._last_os_position_ms)

        if (track_changed or state_changed or position_changed
                or not self._has_anchor):
            self._anchor_position_ms = np.position_ms
            self._anchor_wall_ms = now_ms
            self._has_anchor = True

        self._last_os_position_ms = np.position_ms
        self._is_playing = np.is_playing
        self._artist = np.artist
        self._title = np.title

    def clear(self):
        """No source is playing."""
        self._is_playing = False
        self._artist = ""
        self._title = ""
        self._last_os_position_ms = -1
        self._has_anchor = False

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


def _noop(*args, **kwargs):
    pass


class CueScheduler:
    """Walks a CueFile against a position cursor and fires cues.

    Usage from the main loop::

        scheduler.set_cue_file(cue_file, now_ms=now)
        # each tick:
        scheduler.advance(position_ms, now_ms=now)

    Observer callbacks (both default to no-op):
        on_cue_fire(cue, position_ms)   - every cue admission
        on_lyric(lyric, position_ms)    - every lyric anchor crossed
    """

    __slots__ = (
        "runner",
        "cue_file",
        "_cursor",
        "_lyric_cursor",
        "_last_position_ms",
        "on_cue_fire",
        "on_lyric",
    )

    def __init__(self, runner, *, on_cue_fire=None, on_lyric=None):
        self.runner = runner
        self.cue_file = None
        self._cursor = 0
        self._lyric_cursor = 0
        self._last_position_ms = -1
        self.on_cue_fire = on_cue_fire or _noop
        self.on_lyric = on_lyric or _noop

    def set_cue_file(self, cue_file, now_ms):
        """Switch to a new cue file. Cancels any running FX and
        applies the file-level default FX (if any)."""
        self.cue_file = cue_file
        self._cursor = 0
        self._lyric_cursor = 0
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
        """Fire any cues + lyrics that fall at or before ``position_ms``.

        Handles backward / forward seeks: see module docstring.
        """
        if self.cue_file is None:
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

        # --- Cues -----------------------------------------------------
        if self.cue_file.cues:
            if seek_back:
                self._cursor = 0
            if seek_back or seek_forward:
                # Fire only the most recent cue at-or-before the new position.
                target = None
                while (self._cursor < len(self.cue_file.cues)
                       and self.cue_file.cues[self._cursor].time_ms <= position_ms):
                    target = self.cue_file.cues[self._cursor]
                    self._cursor += 1
                if target is not None:
                    self._fire_cue(target, position_ms, now_ms)
            else:
                while (self._cursor < len(self.cue_file.cues)
                       and self.cue_file.cues[self._cursor].time_ms <= position_ms):
                    cue = self.cue_file.cues[self._cursor]
                    self._cursor += 1
                    self._fire_cue(cue, position_ms, now_ms)

        # --- Lyrics ---------------------------------------------------
        # Same seek-collapse behaviour as cues; a scrubber drag does
        # not dump every intervening line.
        if self.cue_file.lyrics:
            if seek_back:
                self._lyric_cursor = 0
            if seek_back or seek_forward:
                target = None
                while (self._lyric_cursor < len(self.cue_file.lyrics)
                       and self.cue_file.lyrics[self._lyric_cursor].time_ms <= position_ms):
                    target = self.cue_file.lyrics[self._lyric_cursor]
                    self._lyric_cursor += 1
                if target is not None:
                    self.on_lyric(target, position_ms)
            else:
                while (self._lyric_cursor < len(self.cue_file.lyrics)
                       and self.cue_file.lyrics[self._lyric_cursor].time_ms <= position_ms):
                    lyric = self.cue_file.lyrics[self._lyric_cursor]
                    self._lyric_cursor += 1
                    self.on_lyric(lyric, position_ms)

        self._last_position_ms = position_ms

    def _fire_cue(self, cue, position_ms, now_ms):
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
        self.on_cue_fire(cue, position_ms)
