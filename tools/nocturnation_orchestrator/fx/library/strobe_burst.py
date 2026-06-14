"""StrobeBurst - max strobe rate for a short window, then auto-finish.

A drop accent. Master and strobe rate both go max for the burst
duration; everything else is left alone so an underlying wash (set
by a previous FX's release tail) can still bleed through if present.

params:
    [0] duration_100ms  (0..255; default 5 = 500 ms)
    [1] strobe_rate     (0..255; default 255 = max ~4 Hz)
    [2] reserved
    [3] reserved
    [4] reserved
    [5] reserved
"""

from ..base import Fx, set_ch
from ..channels import block_channel, clamp_group, CH_MASTER, CH_STROBE
from ..registry import fx_registry


@fx_registry.register
class StrobeBurst(Fx):
    id = 32
    name = "Strobe Burst"
    cue_name = "strobe_burst"
    category = "drop"
    description = (
        "Max strobe rate for a short window, then auto-finish. A drop "
        "accent. Leaves wash channels alone so an underlying wash from "
        "a previous FX's release tail can still bleed through."
    )

    PARAMS = [
        ("duration",     "100ms", "Burst length in 100 ms units. Default 5 (500 ms)."),
        ("strobe_rate",  "u8",    "Strobe rate (0..255 -> 0..4 Hz). Default 255."),
        ("group",        "count", "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        dur_units = params[0] if params[0] != 0 else 5
        self.default_duration_ms = dur_units * 100
        self._strobe_rate = params[1] if params[1] != 0 else 255
        self._group = clamp_group(params[2] if len(params) > 2 else 0)

    def tick(self, now_ms, universe):
        g = self._group
        set_ch(universe, block_channel(g, CH_MASTER), 255)
        set_ch(universe, block_channel(g, CH_STROBE), self._strobe_rate)
