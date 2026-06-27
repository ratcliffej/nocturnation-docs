"""LinearBuildup - ramp Master + Pulse Probability over buildup_s seconds.

Useful as a "lift" before a drop. Master sweeps from start% to 100%;
pulse probability sweeps from 0 (never fires) to params[3] (the
target probability). The cadence is one pulse per beat at the
supplied BPM.

The FX finishes automatically after `buildup_s` seconds. A YAML cue
typically follows it immediately with the drop FX.

params:
    [0] Pulse R   (default 255 if all RGB zero)
    [1] Pulse G
    [2] Pulse B
    [3] target_probability (0..255 inverted; default 255)
    [4] start_master       (0..255; default 64)
    [5] reserved
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
class LinearBuildup(Fx):
    id = 21
    name = "Linear Buildup"
    cue_name = "linear_buildup"
    category = "buildup"
    description = (
        "Ramps Master + Pulse Probability from start to target over "
        "buildup_s seconds. Fires one pulse per beat. Auto-finishes "
        "after the buildup window so the next cue (typically the drop) "
        "takes over cleanly."
    )

    PARAMS = [
        ("r",                   "u8",      "Pulse Red. White default if all zero."),
        ("g",                   "u8",      "Pulse Green."),
        ("b",                   "u8",      "Pulse Blue."),
        ("target_probability",  "percent", "Probability at end of buildup. Default 100%."),
        ("start_master",        "u8",      "Master at start of buildup. Default 64."),
        ("group",               "count",   "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._beat_ms = max(1, int(round(60_000.0 / bpm)))
        r, g, b = params[0], params[1], params[2]
        if r == 0 and g == 0 and b == 0:
            r, g, b = 255, 255, 255
        self._r, self._g, self._b = r, g, b
        # target_probability is declared "percent" in PARAMS; convert
        # to the 0..255 DMX scalar the StickC mapper buckets into a
        # pixmob::Chance. 0 means "use the default 100%". tick() ramps
        # from 0 -> _target_prob_dmx over the buildup duration.
        self._target_prob_dmx = percent_to_dmx(
            params[3] if params[3] != 0 else 100
        )
        self._start_master = params[4] if params[4] != 0 else 64
        self._group = clamp_group(params[5] if len(params) > 5 else 0)
        # Default duration drives is_finished(); a 0-buildup is a 1 s safety.
        dur_s = buildup_s if buildup_s > 0 else 1
        self.default_duration_ms = dur_s * 1000
        self._dur_ms = self.default_duration_ms
        self._anchor_ms = now_ms - position_ms
        self._last_beat_index = -1

    def tick(self, now_ms, universe):
        # Progress 0.0 -> 1.0 across buildup window. Clamp at 1.0
        # after the duration in case the runner hasn't torn down yet
        # (one or two ticks of overshoot).
        elapsed = now_ms - self._started_ms
        if elapsed < 0:
            elapsed = 0
        progress = elapsed / self._dur_ms
        if progress > 1.0:
            progress = 1.0

        master = self._start_master + int((255 - self._start_master) * progress)
        prob = int(self._target_prob_dmx * progress)

        # Beat cadence (anchored to start time so phase stays stable).
        beat_elapsed = now_ms - self._anchor_ms
        if beat_elapsed < 0:
            beat_elapsed = 0
        beat_index = beat_elapsed // self._beat_ms
        on_beat = beat_index != self._last_beat_index
        self._last_beat_index = beat_index

        g = self._group
        set_ch(universe, block_channel(g, CH_MASTER),     master)
        set_ch(universe, block_channel(g, CH_PULSE_R),    self._r)
        set_ch(universe, block_channel(g, CH_PULSE_G),    self._g)
        set_ch(universe, block_channel(g, CH_PULSE_B),    self._b)
        set_ch(universe, block_channel(g, CH_PULSE_TRIG),
               TRIGGER_HI if on_beat else TRIGGER_LO)
        set_ch(universe, block_channel(g, CH_PULSE_ATK),  16)
        set_ch(universe, block_channel(g, CH_PULSE_SUS),  16)
        set_ch(universe, block_channel(g, CH_PULSE_REL),  64)
        set_ch(universe, block_channel(g, CH_PULSE_PROB), prob)
