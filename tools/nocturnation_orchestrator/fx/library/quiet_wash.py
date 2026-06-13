"""QuietWash - sustained single-colour wash. The default ambient bed.

params:
    [0] R     (0..255)
    [1] G     (0..255)
    [2] B     (0..255)
    [3] intensity (0..255, default 200 when 0)
    [4] master    (0..255, default 255 when 0)
    [5] reserved

Channels written every tick:
    1  Master      <- params[4] or 255
    11 Wash A R    <- params[0]
    12 Wash A G    <- params[1]
    13 Wash A B    <- params[2]
    14 Wash B R    <- 0
    15 Wash B G    <- 0
    16 Wash B B    <- 0
    17 Wash Cycle  <- 0 (hold A; no drift)
    18 Wash Int    <- params[3] or 200
    19 Wash Attack <- 20 (~2 s smooth fade-in on Lume side)
    20 Wash Rel    <- 20 (~2 s smooth release)

Runs indefinitely until cancelled.
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
class QuietWash(Fx):
    id = 1
    name = "Quiet Wash"
    cue_name = "quiet_wash"
    category = "ambient"
    description = (
        "Sustained single-colour wash. Holds Wash A; Wash B is "
        "zeroed so there is no drift cycle. The default ambient bed."
    )

    PARAMS = [
        ("r",         "u8",      "Wash Red (0..255)."),
        ("g",         "u8",      "Wash Green (0..255)."),
        ("b",         "u8",      "Wash Blue (0..255)."),
        ("intensity", "u8",      "Wash intensity (0..255). Default 200 when zero."),
        ("master",    "u8",      "Master scalar (0..255). Default 255 when zero."),
        (None,        None,      "reserved"),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._r = params[0]
        self._g = params[1]
        self._b = params[2]
        self._intensity = params[3] if params[3] != 0 else 200
        self._master = params[4] if params[4] != 0 else 255

    def tick(self, now_ms, universe):
        set_ch(universe, CH_MASTER,      self._master)
        set_ch(universe, CH_WASH_A_R,    self._r)
        set_ch(universe, CH_WASH_A_G,    self._g)
        set_ch(universe, CH_WASH_A_B,    self._b)
        set_ch(universe, CH_WASH_B_R,    0)
        set_ch(universe, CH_WASH_B_G,    0)
        set_ch(universe, CH_WASH_B_B,    0)
        set_ch(universe, CH_WASH_CYCLE,  0)
        set_ch(universe, CH_WASH_INT,    self._intensity)
        set_ch(universe, CH_WASH_ATK,    20)
        set_ch(universe, CH_WASH_REL,    20)
