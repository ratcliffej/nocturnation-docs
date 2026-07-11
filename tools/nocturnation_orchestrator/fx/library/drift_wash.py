"""DriftWash - two-colour wash that cycles A <-> B over the cycle time.

Channels written every tick:
    1  Master      <- 255
    11..13 Wash A RGB
    14..16 Wash B RGB
    17 Wash Cycle  <- params[6] or 80
    18 Wash Int    <- 255
    19 Wash Attack <- 30
    20 Wash Rel    <- 30
    23 Wash Pulse Response <- 255 (allow LIGHT_PULSE to overlay)

Wash on PixMob bracelets is the Director's responsibility, not the
orchestrator's: the StickC's `PixMobIrBinding` (Epic 11) encodes
`LIGHT_WASH` with cycle > 0 into a periodic `SingleColor` refresh
at the live blended A↔B colour. The orchestrator just writes the
wash channels; per-Lume-class encoding decisions live in the binding.
"""

from ..base import Fx, set_ch
from ..channels import (
    block_channel, clamp_group,
    CH_MASTER,
    CH_WASH_A_R, CH_WASH_A_G, CH_WASH_A_B,
    CH_WASH_B_R, CH_WASH_B_G, CH_WASH_B_B,
    CH_WASH_CYCLE, CH_WASH_INT, CH_WASH_ATK, CH_WASH_REL,
    CH_WASH_PULSE_RESPONSE,
)
from ..registry import fx_registry


@fx_registry.register
class DriftWash(Fx):
    id = 2
    name = "Drift Wash"
    cue_name = "drift_wash"
    category = "ambient"
    description = (
        "Two-colour wash that cycles A <-> B over the cycle time. The "
        "Lume fades between anchor A and anchor B; cycle controls the "
        "round-trip duration. Full RGB control on both anchors so any "
        "two-colour drift is expressible (sunset orange -> deep purple, "
        "ice blue -> magenta, etc.)."
    )

    PARAMS = [
        ("a_r",    "u8",    "Anchor A Red (0..255)."),
        ("a_g",    "u8",    "Anchor A Green (0..255)."),
        ("a_b",    "u8",    "Anchor A Blue (0..255)."),
        ("b_r",    "u8",    "Anchor B Red (0..255)."),
        ("b_g",    "u8",    "Anchor B Green (0..255)."),
        ("b_b",    "u8",    "Anchor B Blue (0..255)."),
        ("cycle",  "100ms", "Drift cycle time in 100 ms units (1..255 = "
                            "100 ms..25.5 s). Default 80 (~8 s) when zero."),
        ("group",  "count", "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._ar = params[0]
        self._ag = params[1]
        self._ab = params[2]
        self._br = params[3]
        self._bg = params[4]
        self._bb = params[5]
        self._cycle = params[6] if params[6] != 0 else 80
        self._group = clamp_group(params[7] if len(params) > 7 else 0)

    def tick(self, now_ms, universe):
        g = self._group
        set_ch(universe, block_channel(g, CH_MASTER),     255)
        set_ch(universe, block_channel(g, CH_WASH_A_R),   self._ar)
        set_ch(universe, block_channel(g, CH_WASH_A_G),   self._ag)
        set_ch(universe, block_channel(g, CH_WASH_A_B),   self._ab)
        set_ch(universe, block_channel(g, CH_WASH_B_R),   self._br)
        set_ch(universe, block_channel(g, CH_WASH_B_G),   self._bg)
        set_ch(universe, block_channel(g, CH_WASH_B_B),   self._bb)
        set_ch(universe, block_channel(g, CH_WASH_CYCLE), self._cycle)
        # Wire intensity at max. Full-mode Tildagon renders authored
        # colours 1:1; Calm mode + LedStrip device-brightness cap still
        # attenuate on their side. Prior value 220 was an arbitrary
        # ~14 % attenuation with no documented rationale that dialled
        # Full-mode Tildagon wash below authored - fixed 2026-07-09.
        set_ch(universe, block_channel(g, CH_WASH_INT),   255)
        set_ch(universe, block_channel(g, CH_WASH_ATK),   30)
        set_ch(universe, block_channel(g, CH_WASH_REL),   30)
        # Allow pulse cues to overlay; without this the Lume's
        # perimeter renderer drops every LIGHT_PULSE arriving while
        # this wash is in ATTACK / HOLD.
        set_ch(universe, block_channel(g, CH_WASH_PULSE_RESPONSE), 255)
