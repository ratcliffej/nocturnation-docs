"""DriftWash - two-colour wash that cycles A <-> B over the cycle time.

params:
    [0] A.R   (0..255)
    [1] A.G   (0..255)
    [2] A.B   (0..255)
    [3] B.R   (0..255)
    [4] B.G   (0..255)
    [5] cycle_time_100ms (0..255; on-wire 100ms units; default 80 = ~8 s)

B.B is derived from B.R+B.G being non-zero - we leave one component
free per palette and pick the third channel value such that the user
can simply set the two main colour components and pick up a sensible
default. For now: B.B = 0 unless both B.R and B.G are 0, in which
case the wash collapses to a hold of A (cycle has no effect at that
point).

Channels written every tick:
    1  Master      <- 255
    11..13 Wash A RGB
    14..16 Wash B RGB
    17 Wash Cycle  <- params[5] or 80
    18 Wash Int    <- 220
    19 Wash Attack <- 30
    20 Wash Rel    <- 30
"""

from ..base import Fx, set_ch
from ..channels import (
    CH_MASTER,
    CH_WASH_A_R, CH_WASH_A_G, CH_WASH_A_B,
    CH_WASH_B_R, CH_WASH_B_G, CH_WASH_B_B,
    CH_WASH_CYCLE, CH_WASH_INT, CH_WASH_ATK, CH_WASH_REL,
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
        "round-trip duration."
    )

    PARAMS = [
        ("a_r",    "u8",    "Anchor A Red (0..255)."),
        ("a_g",    "u8",    "Anchor A Green (0..255)."),
        (None,     None,    "reserved"),
        ("b_r",    "u8",    "Anchor B Red (0..255)."),
        ("b_g",    "u8",    "Anchor B Green (0..255)."),
        ("cycle",  "100ms", "Drift cycle time in 100 ms units (1..255 = "
                            "100 ms..25.5 s). Default 80 (~8 s) when zero."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._ar = params[0]
        self._ag = params[1]
        self._ab = 0  # caller can't specify A.B without burning a param;
                      # held to 0 for the v1 surface.
        self._br = params[3]
        self._bg = params[4]
        self._bb = 0
        self._cycle = params[5] if params[5] != 0 else 80

    def tick(self, now_ms, universe):
        set_ch(universe, CH_MASTER,     255)
        set_ch(universe, CH_WASH_A_R,   self._ar)
        set_ch(universe, CH_WASH_A_G,   self._ag)
        set_ch(universe, CH_WASH_A_B,   self._ab)
        set_ch(universe, CH_WASH_B_R,   self._br)
        set_ch(universe, CH_WASH_B_G,   self._bg)
        set_ch(universe, CH_WASH_B_B,   self._bb)
        set_ch(universe, CH_WASH_CYCLE, self._cycle)
        set_ch(universe, CH_WASH_INT,   220)
        set_ch(universe, CH_WASH_ATK,   30)
        set_ch(universe, CH_WASH_REL,   30)
