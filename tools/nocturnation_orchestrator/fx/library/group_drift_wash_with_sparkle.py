"""GroupDriftWashWithSparkle - phase-offset drift wash across groups
plus a cascading sparkle on the beat.

Runs a continuous A<->B drift wash on each of groups 1..num_groups.
Each group's cycle phase is offset by (group_index / num_groups) of
the cycle time, so on a static display where groups 1..N are physically
in order, the wash reads as a moving colour wave flowing across the
groups. Sparkles rotate one group per beat like group_cascade -
depending on the ratio of BPM to cycle time the sparkle can chase,
lead, or wander relative to the wave peak.

Wire strategy - minimises airtime:
    - Each group's wash channels are written ONCE at t = (g-1)/N * cycle.
      The StickC mapper detects the change and emits one LIGHT_WASH
      per group; the Lume drifts locally from that anchor point, so
      the phase offset is baked in by the delay between emissions.
    - Pulse channels are written every tick to all num_groups blocks
      (constant RGB, TRIGGER_HI held for HOLD_MS on the active-beat
      group only). PULSE_TRIG rising edges are what the mapper turns
      into LIGHT_PULSE, so the wire cost is one LIGHT_PULSE per beat.
    - Broadcast block (0) is left untouched - a previous FX's broadcast
      wash can survive underneath if the LD wants it, matching
      group_cascade's convention.

Beat cadence anchors to (now_ms - position_ms) so late-join / seek
holds the phase. Beat-grid sidecar (beats_ms) drives the sparkle when
available for tempo-drift resilience, mirroring wash_with_sparkle.

params:
    [0..2]  a_r, a_g, a_b       Wash anchor A RGB.
    [3..5]  b_r, b_g, b_b       Wash anchor B RGB.
    [6]     cycle               Wash cycle in 100 ms units (default 80 = 8 s).
    [7..9]  s_r, s_g, s_b       Sparkle RGB (all-zero -> white default).
    [10]    probability         Sparkle chance per beat (percent).
    [11]    num_groups          1..9; default 4.
    [12]    attack              Sparkle envelope attack (default 16).
    [13]    sustain             Sparkle envelope sustain (default 16).
    [14]    release             Sparkle envelope release (default 96).

Channels written per tick:
    On each group g in 1..num_groups:
        block-local 1   Master         <- 255 (every tick)
        block-local 3..5 Pulse RGB     <- sparkle colour (every tick)
        block-local 6   Pulse Trigger  <- HI on active-beat group only
        block-local 7..9 ASR           <- attack/sustain/release
        block-local 10  Probability    <- probability
    Once per group at scheduled offset:
        block-local 11..16 Wash A/B RGB
        block-local 17    Wash Cycle
        block-local 18    Wash Intensity
        block-local 19..20 Wash Attack/Release
        block-local 23    Wash Pulse Response <- 255
"""

from ..base import Fx, set_ch
from ..channels import (
    block_channel,
    CH_MASTER,
    CH_PULSE_R, CH_PULSE_G, CH_PULSE_B,
    CH_PULSE_TRIG, CH_PULSE_ATK, CH_PULSE_SUS, CH_PULSE_REL, CH_PULSE_PROB,
    CH_WASH_A_R, CH_WASH_A_G, CH_WASH_A_B,
    CH_WASH_B_R, CH_WASH_B_G, CH_WASH_B_B,
    CH_WASH_CYCLE, CH_WASH_INT, CH_WASH_ATK, CH_WASH_REL,
    CH_WASH_PULSE_RESPONSE,
    TRIGGER_HI, TRIGGER_LO,
)
from ..registry import fx_registry


@fx_registry.register
class GroupDriftWashWithSparkle(Fx):
    id = 16
    name = "Group Drift Wash With Sparkle"
    cue_name = "group_drift_wash_with_sparkle"
    category = "beat"
    description = (
        "Phase-offset drift wash across groups 1..num_groups plus a "
        "cascading sparkle one group per beat. On a static display "
        "with groups physically in order this reads as a moving "
        "colour wave. Each group's wash is emitted once at its "
        "scheduled phase offset; the Lume drifts locally, so wire "
        "traffic stays low."
    )

    PARAMS = [
        ("a_r",         "u8",      "Wash anchor A Red."),
        ("a_g",         "u8",      "Wash anchor A Green."),
        ("a_b",         "u8",      "Wash anchor A Blue."),
        ("b_r",         "u8",      "Wash anchor B Red."),
        ("b_g",         "u8",      "Wash anchor B Green."),
        ("b_b",         "u8",      "Wash anchor B Blue."),
        ("cycle",       "100ms",   "Wash cycle time. Default 80 (~8 s) when zero."),
        ("s_r",         "u8",      "Sparkle Red. White default if all sparkle RGB zero."),
        ("s_g",         "u8",      "Sparkle Green."),
        ("s_b",         "u8",      "Sparkle Blue."),
        ("probability", "percent", "Sparkle chance per beat (0..100%). Default 100%."),
        ("num_groups",  "count",   "Groups to cascade across (1..9). Default 4."),
        ("attack",      "u8",      "Sparkle attack slider 0..255. Default 16 when zero."),
        ("sustain",     "u8",      "Sparkle sustain slider 0..255. Default 16 when zero."),
        ("release",     "u8",      "Sparkle release slider 0..255. Default 96 when zero."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        # Wash anchors + cycle. cycle is in 100 ms units for the wire; we
        # also cache ms form for the phase-offset scheduling below.
        self._ar, self._ag, self._ab = params[0], params[1], params[2]
        self._br, self._bg, self._bb = params[3], params[4], params[5]
        self._cycle_100ms = params[6] if params[6] != 0 else 80
        self._cycle_ms    = self._cycle_100ms * 100
        # Sparkle colour. All-zero -> white so an undermade cue still
        # produces visible beat output.
        sr, sg, sb = params[7], params[8], params[9]
        if sr == 0 and sg == 0 and sb == 0:
            sr, sg, sb = 255, 255, 255
        self._sr, self._sg, self._sb = sr, sg, sb
        # to_u8() in cues.py already ran percent->u8 on this slot
        # (declared "percent" in PARAMS). Take verbatim; 0 -> 255 (100%).
        self._prob = params[10] if params[10] != 0 else 255
        # Group count. Clamp to 1..9 (block 0 broadcast is deliberately
        # unused - see docstring).
        n = params[11] if len(params) > 11 and params[11] != 0 else 4
        if n < 1:
            n = 1
        elif n > 9:
            n = 9
        self._num_groups = n
        # Sparkle envelope (matches wash_with_sparkle defaults).
        self._atk_slider = params[12] if (len(params) > 12 and params[12] != 0) else 16
        self._sus_slider = params[13] if (len(params) > 13 and params[13] != 0) else 16
        self._rel_slider = params[14] if (len(params) > 14 and params[14] != 0) else 96
        # Per-group wash-send schedule. Group g in 1..N fires its
        # LIGHT_WASH at elapsed = (g-1)/N * cycle_ms, so their local
        # Lume-side drift phases end up offset by 1/N of the cycle.
        # send_at_elapsed is indexed by (g - 1); sent[i] flips on the
        # tick that actually writes to the block, so re-writes are
        # skipped on subsequent ticks (mapper would ignore them anyway,
        # but this keeps tick() cheap under high group counts).
        self._send_at_elapsed = [
            (g - 1) * self._cycle_ms // n for g in range(1, n + 1)
        ]
        self._sent = [False] * n
        # DMX-bridge last-wins workaround (see wash_with_sparkle.tick
        # for full history); hold PULSE_TRIG HI for HOLD_MS so a
        # bridge poll can't miss the rising edge.
        self._hi_until_ms = 0
        # Beat cadence + optional beat-grid sidecar (Epic 14.9 Block B).
        self._beat_ms = max(1, int(round(60_000.0 / bpm)))
        self._beat_anchor_ms = now_ms - position_ms
        self._beats_ms = list(getattr(self, "beats_ms", None) or [])
        self._use_beats = bool(self._beats_ms)
        if self._use_beats:
            self._last_beat_index = (
                self._count_beats_at_or_before(position_ms) - 1
            )
        else:
            self._last_beat_index = -1

    def _count_beats_at_or_before(self, t_ms):
        """Binary search the count of beats whose timestamp is <= t_ms.
        Shared shape with sparkle_on_beat / pulse_per_bar /
        wash_with_sparkle; if a fourth adopter arrives promote to a
        shared helper."""
        lo, hi = 0, len(self._beats_ms)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._beats_ms[mid] <= t_ms:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def tick(self, now_ms, universe):
        elapsed = now_ms - self._started_ms
        if elapsed < 0:
            elapsed = 0

        # ------------------------------------------------------------------
        # Wash channels: one-shot per group at their scheduled phase offset.
        for i in range(self._num_groups):
            if self._sent[i] or elapsed < self._send_at_elapsed[i]:
                continue
            g = i + 1
            set_ch(universe, block_channel(g, CH_WASH_A_R),   self._ar)
            set_ch(universe, block_channel(g, CH_WASH_A_G),   self._ag)
            set_ch(universe, block_channel(g, CH_WASH_A_B),   self._ab)
            set_ch(universe, block_channel(g, CH_WASH_B_R),   self._br)
            set_ch(universe, block_channel(g, CH_WASH_B_G),   self._bg)
            set_ch(universe, block_channel(g, CH_WASH_B_B),   self._bb)
            set_ch(universe, block_channel(g, CH_WASH_CYCLE), self._cycle_100ms)
            set_ch(universe, block_channel(g, CH_WASH_INT),   220)
            set_ch(universe, block_channel(g, CH_WASH_ATK),   30)
            set_ch(universe, block_channel(g, CH_WASH_REL),   30)
            # Allow LIGHT_PULSE to overlay on top of the wash (Fix #7).
            set_ch(universe, block_channel(g, CH_WASH_PULSE_RESPONSE), 255)
            self._sent[i] = True

        # ------------------------------------------------------------------
        # Beat detection - prefer music-position (pause / seek aware).
        music_pos = getattr(self, "position_ms", None)
        if self._use_beats and music_pos is not None:
            beat_index = self._count_beats_at_or_before(music_pos) - 1
        elif self._use_beats:
            beat_elapsed = now_ms - self._beat_anchor_ms
            if beat_elapsed < 0:
                beat_elapsed = 0
            beat_index = self._count_beats_at_or_before(beat_elapsed) - 1
        else:
            beat_elapsed = now_ms - self._beat_anchor_ms
            if beat_elapsed < 0:
                beat_elapsed = 0
            beat_index = beat_elapsed // self._beat_ms
        on_beat = beat_index != self._last_beat_index
        self._last_beat_index = beat_index

        # Sparkle cursor: rotates one group per beat like group_cascade.
        # Clamp beat_index to a non-negative int so the modulo is safe
        # for the pre-first-beat window when the beat-grid path can
        # return -1.
        cursor_index = beat_index if beat_index >= 0 else 0
        active_group = (int(cursor_index) % self._num_groups) + 1

        # HOLD_MS = 100 for the StickC bridge last-wins workaround. See
        # wash_with_sparkle.tick for full rationale.
        HOLD_MS = 100
        if on_beat:
            self._hi_until_ms = now_ms + HOLD_MS
        hi_now = now_ms < self._hi_until_ms

        # ------------------------------------------------------------------
        # Pulse channels + Master: written every tick on all groups so
        # the mapper picks up any change immediately. Master stays at
        # 255 across the fleet so the wash + sparkle both render at
        # full intensity per-group scaling.
        for i in range(self._num_groups):
            g = i + 1
            set_ch(universe, block_channel(g, CH_MASTER),     255)
            set_ch(universe, block_channel(g, CH_PULSE_R),    self._sr)
            set_ch(universe, block_channel(g, CH_PULSE_G),    self._sg)
            set_ch(universe, block_channel(g, CH_PULSE_B),    self._sb)
            fire = hi_now and (g == active_group)
            set_ch(universe, block_channel(g, CH_PULSE_TRIG),
                   TRIGGER_HI if fire else TRIGGER_LO)
            set_ch(universe, block_channel(g, CH_PULSE_ATK),  self._atk_slider)
            set_ch(universe, block_channel(g, CH_PULSE_SUS),  self._sus_slider)
            set_ch(universe, block_channel(g, CH_PULSE_REL),  self._rel_slider)
            set_ch(universe, block_channel(g, CH_PULSE_PROB), self._prob)
