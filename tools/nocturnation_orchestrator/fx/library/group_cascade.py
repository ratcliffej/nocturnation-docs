"""GroupCascade - rotates a pulse around groups 1..N, one beat per group.

Hits group 1 on beat 0, group 2 on beat 1, ..., wrapping back to 1.
Each group's pulse trigger lives in its own DMX block (group N at
universe ch 6 + N*40). Broadcast (block 0) is left untouched so the
ambient wash on the broadcast block survives.

params:
    [0] Pulse R   (default 255 if all RGB zero)
    [1] Pulse G
    [2] Pulse B
    [3] Probability (default 255)
    [4] num_groups (default 4; clamped to 1..9)
    [5] reserved

Channels written per tick on EACH group's block (1..num_groups):
    block-local 1  Master         <- 255
    block-local 3..5 Pulse RGB    <- same params for all groups
    block-local 6  Pulse Trigger  <- 255 only for the active beat group
    block-local 7..9 ASR          <- 16 / 16 / 96
    block-local 10 Probability    <- params[3] or 255
"""

from ..base import Fx, set_ch
from ..channels import (
    percent_to_dmx,
    block_channel,
    CH_MASTER,
    CH_PULSE_R, CH_PULSE_G, CH_PULSE_B,
    CH_PULSE_TRIG, CH_PULSE_ATK, CH_PULSE_SUS, CH_PULSE_REL, CH_PULSE_PROB,
    TRIGGER_HI, TRIGGER_LO,
)
from ..registry import fx_registry


@fx_registry.register
class GroupCascade(Fx):
    id = 13
    name = "Group Cascade"
    cue_name = "group_cascade"
    category = "beat"
    description = (
        "Rotates a pulse around groups 1..N, one beat per group. "
        "Broadcast (block 0) is left untouched so an ambient wash on the "
        "broadcast block survives underneath."
    )

    PARAMS = [
        ("r",           "u8",      "Pulse Red. White default if all zero."),
        ("g",           "u8",      "Pulse Green."),
        ("b",           "u8",      "Pulse Blue."),
        ("probability", "percent", "Chance per beat (0..100%). Default 100%."),
        ("num_groups",  "count",   "Groups to cascade across (1..9). Default 4."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._beat_ms = max(1, int(round(60_000.0 / bpm)))
        r, g, b = params[0], params[1], params[2]
        if r == 0 and g == 0 and b == 0:
            r, g, b = 255, 255, 255
        self._r, self._g, self._b = r, g, b
        self._prob = percent_to_dmx(params[3] if params[3] != 0 else 100)
        n = params[4] if params[4] != 0 else 4
        if n < 1:
            n = 1
        elif n > 9:
            n = 9
        self._num_groups = n
        self._anchor_ms = now_ms - position_ms
        self._last_beat_index = -1

    def tick(self, now_ms, universe):
        elapsed = now_ms - self._anchor_ms
        if elapsed < 0:
            elapsed = 0
        beat_index = elapsed // self._beat_ms
        on_beat = beat_index != self._last_beat_index
        self._last_beat_index = beat_index

        # Which group fires on this beat (1..num_groups).
        active_group = (beat_index % self._num_groups) + 1

        for grp in range(1, self._num_groups + 1):
            set_ch(universe, block_channel(grp, CH_MASTER),      255)
            set_ch(universe, block_channel(grp, CH_PULSE_R),     self._r)
            set_ch(universe, block_channel(grp, CH_PULSE_G),     self._g)
            set_ch(universe, block_channel(grp, CH_PULSE_B),     self._b)
            fire = on_beat and grp == active_group
            set_ch(universe, block_channel(grp, CH_PULSE_TRIG),
                   TRIGGER_HI if fire else TRIGGER_LO)
            set_ch(universe, block_channel(grp, CH_PULSE_ATK),   16)
            set_ch(universe, block_channel(grp, CH_PULSE_SUS),   16)
            set_ch(universe, block_channel(grp, CH_PULSE_REL),   96)
            set_ch(universe, block_channel(grp, CH_PULSE_PROB),  self._prob)
