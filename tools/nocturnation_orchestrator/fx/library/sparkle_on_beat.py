"""SparkleOnBeat - fires one pulse per beat at the supplied BPM.

The pulse goes out to all groups (broadcast). Each beat: write
trigger high for one tick, then low. The DMX bridge's rising-edge
detector picks the rising 0->255 between consecutive ticks and emits
one LIGHT_PULSE wire frame; the trigger goes back to 0 on the next
tick to re-arm.

params:
    [0] Pulse R   (default 255 if all RGB zero)
    [1] Pulse G
    [2] Pulse B
    [3] Probability  (0..255 inverted-mapping; default 255 = always fire)
    [4] reserved
    [5] reserved

Channels written every tick:
    1  Master         <- 255 (caller may want to layer; for B3 we set it)
    3..5 Pulse RGB    <- params[0..2]
    6  Pulse Trigger  <- 255 on a beat tick, 0 otherwise
    7  Pulse Attack   <- 16  (short)
    8  Pulse Sustain  <- 16
    9  Pulse Release  <- 96  (longer for sparkle tail)
    10 Pulse Prob     <- params[3] or 255

The pulse-cadence calculation uses the start-time anchor + BPM so
phase is stable across late-join (position_ms).
"""

from ..base import Fx, set_ch
from ..channels import (
    block_channel, clamp_group, percent_to_dmx,
    CH_MASTER,
    CH_PULSE_R, CH_PULSE_G, CH_PULSE_B,
    CH_PULSE_TRIG, CH_PULSE_ATK, CH_PULSE_SUS, CH_PULSE_REL, CH_PULSE_PROB,
    TRIGGER_HI, TRIGGER_LO,
)
from ..registry import fx_registry


@fx_registry.register
class SparkleOnBeat(Fx):
    id = 11
    name = "Sparkle On Beat"
    cue_name = "sparkle_on_beat"
    category = "beat"
    description = (
        "Fires one pulse per beat at the supplied BPM. Broadcast (all "
        "groups). Sits naturally on top of a previous ambient FX's "
        "release tail."
    )

    PARAMS = [
        ("r",           "u8",      "Pulse Red. White default if R/G/B all zero."),
        ("g",           "u8",      "Pulse Green."),
        ("b",           "u8",      "Pulse Blue."),
        ("probability", "percent", "Chance per beat (0..100%). Default 100%."),
        ("group",       "count",   "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        # bpm comes from runner default (120) when 0; safe to assume non-zero.
        self._beat_ms = max(1, int(round(60_000.0 / bpm)))
        r, g, b = params[0], params[1], params[2]
        if r == 0 and g == 0 and b == 0:
            r, g, b = 255, 255, 255
        self._r, self._g, self._b = r, g, b
        # Convert the percent-declared param to the DMX 0..255 scalar
        # the StickC mapper's quantize_chance() expects. 0 means
        # "use the default 100%"; clamp behaviour lives in
        # percent_to_dmx so out-of-range cues degrade gracefully.
        self._prob = percent_to_dmx(params[3] if params[3] != 0 else 100)
        self._group = clamp_group(params[4] if len(params) > 4 else 0)
        # Beat phase: align the first beat to `now_ms - position_ms` so
        # late-join keeps the same on-the-beat timing.
        self._beat_anchor_ms = now_ms - position_ms
        # Bench-found bug workaround 2026-06-28: StickC DMX bridge
        # is last-wins on universes. Holding TRIGGER_HI on the wire
        # for 100 ms ensures the bridge poll catches the rising edge.
        # See wash_with_sparkle.start for the full rationale.
        self._hi_until_ms = 0
        # Epic 14.9 Block B. The runner attaches `self.beats_ms` from
        # the analysis sidecar before calling start(). When non-empty
        # we run the FX off the actual beat grid instead of bpm /
        # song-start. Eliminates the phase error for songs with
        # pickup silence or rubato.
        self._beats_ms = list(getattr(self, "beats_ms", None) or [])
        self._use_beats = bool(self._beats_ms)
        if self._use_beats:
            # Pre-seed _last_beat_index to the most recent beat at or
            # before the song's current position; otherwise the FX
            # would retro-fire every elapsed beat on its first tick.
            self._last_beat_index = (
                self._count_beats_at_or_before(position_ms) - 1
            )
        else:
            self._last_beat_index = -1

    def _count_beats_at_or_before(self, t_ms):
        """Binary search the count of beats whose timestamp is <= t_ms."""
        lo, hi = 0, len(self._beats_ms)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._beats_ms[mid] <= t_ms:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def tick(self, now_ms, universe):
        # Prefer the music-position-driven beat index (set by the
        # runner from tracker.current_position). When music pauses,
        # position_ms freezes and so does the beat index - which is
        # the correct behaviour for a music-cue system. Falls back
        # to wall-clock-elapsed when no position is attached (host
        # tests, callers that haven't been updated to pass
        # position_ms through runner.tick).
        music_pos = getattr(self, "position_ms", None)
        if self._use_beats and music_pos is not None:
            beat_index = self._count_beats_at_or_before(music_pos) - 1
        elif self._use_beats:
            elapsed = now_ms - self._beat_anchor_ms
            if elapsed < 0:
                elapsed = 0
            beat_index = self._count_beats_at_or_before(elapsed) - 1
        else:
            elapsed = now_ms - self._beat_anchor_ms
            if elapsed < 0:
                elapsed = 0
            beat_index = elapsed // self._beat_ms
        on_beat = beat_index != self._last_beat_index
        self._last_beat_index = beat_index
        # Hold TRIGGER_HI for 100 ms so the StickC bridge's last-wins
        # poll catches the rising edge. See wash_with_sparkle for full
        # rationale.
        HOLD_MS = 100
        if on_beat:
            self._hi_until_ms = now_ms + HOLD_MS

        g = self._group
        set_ch(universe, block_channel(g, CH_MASTER),     255)
        set_ch(universe, block_channel(g, CH_PULSE_R),    self._r)
        set_ch(universe, block_channel(g, CH_PULSE_G),    self._g)
        set_ch(universe, block_channel(g, CH_PULSE_B),    self._b)
        set_ch(universe, block_channel(g, CH_PULSE_TRIG),
               TRIGGER_HI if now_ms < self._hi_until_ms else TRIGGER_LO)
        set_ch(universe, block_channel(g, CH_PULSE_ATK),  16)
        set_ch(universe, block_channel(g, CH_PULSE_SUS),  16)
        set_ch(universe, block_channel(g, CH_PULSE_REL),  96)
        set_ch(universe, block_channel(g, CH_PULSE_PROB), self._prob)
