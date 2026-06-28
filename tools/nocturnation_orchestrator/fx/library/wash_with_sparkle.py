"""WashWithSparkle - drift wash + sparkle on beat in one FX.

Layered effect that runs both the wash and the sparkle pulse pattern
from a single cue. Eliminates the need for runner-level FX layering
for the common "ambient bed + beat texture" composition.

Channels written every tick:
    1  Master         <- 255
    3..5 Pulse RGB    <- params[7..9] (sparkle colour)
    6  Pulse Trigger  <- 255 on a beat tick, 0 otherwise
    7..9 Pulse ASR    <- 16 / 16 / 96
    10 Pulse Prob     <- params[10] (sparkle probability)
    11..13 Wash A RGB <- params[0..2]
    14..16 Wash B RGB <- params[3..5]
    17 Wash Cycle     <- params[6] or 80
    18 Wash Int       <- 220
    19 Wash Attack    <- 30
    20 Wash Release   <- 30
    23 Wash Pulse Response <- 255 (allow LIGHT_PULSE to overlay)

Wash + sparkle on PixMob bracelets is the Director's responsibility,
not the orchestrator's: the StickC's `PixMobIrBinding` (Epic 11)
encodes `LIGHT_WASH` into a periodic `SingleColor` refresh and maps
`LIGHT_WASH_PULSE` (which is what a sparkle on a wash becomes for
capable Lumes) into a `TwoColors(sparkle, wash)` composite. This FX
just writes the wash + pulse channels; the per-Lume-class encoding
lives in the binding.

Beat cadence is anchored to (now_ms - position_ms) so late-join
holds the phase.
"""

from ..base import Fx, set_ch
from ..channels import (
    percent_to_dmx,
    block_channel, clamp_group,
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
class WashWithSparkle(Fx):
    id = 14
    name = "Wash With Sparkle"
    cue_name = "wash_with_sparkle"
    category = "beat"
    description = (
        "Layered drift wash + sparkle-on-beat in a single FX. The wash "
        "cycles between anchors A and B over the cycle time; the "
        "sparkle fires a pulse on each beat at the supplied BPM. "
        "Single cue for the most-common ambient-bed + beat-texture "
        "composition."
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
        ("group",       "count",   "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
        ("attack",      "u8",      "Sparkle attack slider 0..255 (pixmob_time bucketed). Default 16 when zero."),
        ("sustain",     "u8",      "Sparkle sustain slider 0..255. Default 16 when zero."),
        ("release",     "u8",      "Sparkle release slider 0..255. Default 96 when zero."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        # Wash anchors + cycle.
        self._ar, self._ag, self._ab = params[0], params[1], params[2]
        self._br, self._bg, self._bb = params[3], params[4], params[5]
        self._cycle = params[6] if params[6] != 0 else 80
        # Sparkle colour. All-zero -> white so an undermade cue still
        # produces visible beat output.
        sr, sg, sb = params[7], params[8], params[9]
        if sr == 0 and sg == 0 and sb == 0:
            sr, sg, sb = 255, 255, 255
        self._sr, self._sg, self._sb = sr, sg, sb
        self._prob = percent_to_dmx(params[10] if params[10] != 0 else 100)
        self._group = clamp_group(params[11] if len(params) > 11 else 0)
        # Sparkle envelope (Epic 14.9 bench follow-up: was hardcoded
        # 16/16/96 = 0+0+192 ms via pixmob_time bucketing. Operator
        # wanted to test "what if envelope tail overlaps next beat".
        # Defaults preserve the legacy behaviour; explicit non-zero
        # values override. Slider math: bucket_idx = slider // 32,
        # bucket_ms in (0, 32, 96, 192, 480, 960, 2400, 3840). For a
        # near-instant on/off pulse, try attack=1 sustain=1 release=32
        # which gives 0+0+32 = 32 ms.
        self._atk_slider = params[12] if (len(params) > 12 and params[12] != 0) else 16
        self._sus_slider = params[13] if (len(params) > 13 and params[13] != 0) else 16
        self._rel_slider = params[14] if (len(params) > 14 and params[14] != 0) else 96
        # Beat cadence. Epic 14.9 Block B: when the runner has attached
        # a beats_ms list (loaded from <name>.cues.analysis.json), drive
        # the sparkle from the actual librosa beat grid rather than the
        # bpm-derived clock. This is critical when @bpm doesn't quite
        # match librosa's measured tempo (e.g. 140 declared, 139.67
        # actual = ~90 ms phase offset per beat at runtime) or when the
        # song has any tempo variation. Falls back to bpm-clock when
        # no sidecar is loaded (host tests, devs without librosa, etc.)
        # so the FX still works in those environments.
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
        Mirrors sparkle_on_beat / pulse_per_bar; could be promoted to
        a shared helper if more FX adopt beat-grid awareness."""
        lo, hi = 0, len(self._beats_ms)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._beats_ms[mid] <= t_ms:
                lo = mid + 1
            else:
                hi = mid
        return lo

    def tick(self, now_ms, universe):
        g = self._group
        # Wash (continuous).
        set_ch(universe, block_channel(g, CH_MASTER),     255)
        set_ch(universe, block_channel(g, CH_WASH_A_R),   self._ar)
        set_ch(universe, block_channel(g, CH_WASH_A_G),   self._ag)
        set_ch(universe, block_channel(g, CH_WASH_A_B),   self._ab)
        set_ch(universe, block_channel(g, CH_WASH_B_R),   self._br)
        set_ch(universe, block_channel(g, CH_WASH_B_G),   self._bg)
        set_ch(universe, block_channel(g, CH_WASH_B_B),   self._bb)
        set_ch(universe, block_channel(g, CH_WASH_CYCLE), self._cycle)
        set_ch(universe, block_channel(g, CH_WASH_INT),   220)
        set_ch(universe, block_channel(g, CH_WASH_ATK),   30)
        set_ch(universe, block_channel(g, CH_WASH_REL),   30)
        # Allow LIGHT_PULSE to overlay on top of this wash; without
        # this the Lume drops every subsequent pulse cue silently.
        # See render/perimeter.py dispatch() guard on pulse_response.
        set_ch(universe, block_channel(g, CH_WASH_PULSE_RESPONSE), 255)
        # Sparkle (beat-aligned rising edge). Prefer music-position
        # over wall-clock - badge keeps pulsing through a pause
        # otherwise. See sparkle_on_beat.tick for the same shape.
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
        set_ch(universe, block_channel(g, CH_PULSE_R),    self._sr)
        set_ch(universe, block_channel(g, CH_PULSE_G),    self._sg)
        set_ch(universe, block_channel(g, CH_PULSE_B),    self._sb)
        set_ch(universe, block_channel(g, CH_PULSE_TRIG),
               TRIGGER_HI if on_beat else TRIGGER_LO)
        set_ch(universe, block_channel(g, CH_PULSE_ATK),  self._atk_slider)
        set_ch(universe, block_channel(g, CH_PULSE_SUS),  self._sus_slider)
        set_ch(universe, block_channel(g, CH_PULSE_REL),  self._rel_slider)
        set_ch(universe, block_channel(g, CH_PULSE_PROB), self._prob)
