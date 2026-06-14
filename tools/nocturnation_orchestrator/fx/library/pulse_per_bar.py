"""PulsePerBar - fires one pulse per bar (4 beats by default).

Same channel surface as SparkleOnBeat, but the cadence is one pulse
every N beats. Useful for anchor pulses on top of a wash without the
constant sparkle texture.

params:
    [0] Pulse R   (default 255 if all RGB zero)
    [1] Pulse G
    [2] Pulse B
    [3] Probability (default 255)
    [4] beats_per_bar (default 4)
    [5] reserved
"""

from ..base import Fx, set_ch
from ..channels import (
    block_channel, clamp_group,
    CH_MASTER,
    CH_PULSE_R, CH_PULSE_G, CH_PULSE_B,
    CH_PULSE_TRIG, CH_PULSE_ATK, CH_PULSE_SUS, CH_PULSE_REL, CH_PULSE_PROB,
    TRIGGER_HI, TRIGGER_LO,
)
from ..registry import fx_registry


@fx_registry.register
class PulsePerBar(Fx):
    id = 12
    name = "Pulse Per Bar"
    cue_name = "pulse_per_bar"
    category = "beat"
    description = (
        "Fires one pulse every N beats (default 4 = one per bar in 4/4). "
        "Useful for anchor pulses on top of a wash without the constant "
        "sparkle texture."
    )

    PARAMS = [
        ("r",             "u8",      "Pulse Red. White default if all zero."),
        ("g",             "u8",      "Pulse Green."),
        ("b",             "u8",      "Pulse Blue."),
        ("probability",   "percent", "Chance per bar (0..100%). Default 100%."),
        ("beats_per_bar", "count",   "Beats between pulses (1..16). Default 4."),
        ("group",         "count",   "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        beats_per_bar = params[4] if params[4] != 0 else 4
        beat_ms = max(1, int(round(60_000.0 / bpm)))
        self._bar_ms = beat_ms * beats_per_bar
        r, g, b = params[0], params[1], params[2]
        if r == 0 and g == 0 and b == 0:
            r, g, b = 255, 255, 255
        self._r, self._g, self._b = r, g, b
        self._prob = params[3] if params[3] != 0 else 255
        self._group = clamp_group(params[5] if len(params) > 5 else 0)
        self._anchor_ms = now_ms - position_ms
        self._last_bar_index = -1

    def tick(self, now_ms, universe):
        elapsed = now_ms - self._anchor_ms
        if elapsed < 0:
            elapsed = 0
        bar_index = elapsed // self._bar_ms
        on_bar = bar_index != self._last_bar_index
        self._last_bar_index = bar_index

        g = self._group
        set_ch(universe, block_channel(g, CH_MASTER),     255)
        set_ch(universe, block_channel(g, CH_PULSE_R),    self._r)
        set_ch(universe, block_channel(g, CH_PULSE_G),    self._g)
        set_ch(universe, block_channel(g, CH_PULSE_B),    self._b)
        set_ch(universe, block_channel(g, CH_PULSE_TRIG),
               TRIGGER_HI if on_bar else TRIGGER_LO)
        set_ch(universe, block_channel(g, CH_PULSE_ATK),  32)
        set_ch(universe, block_channel(g, CH_PULSE_SUS),  64)
        set_ch(universe, block_channel(g, CH_PULSE_REL),  128)
        set_ch(universe, block_channel(g, CH_PULSE_PROB), self._prob)
