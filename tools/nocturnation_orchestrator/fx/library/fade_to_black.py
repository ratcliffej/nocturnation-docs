"""FadeToBlack - ramp Master from 255 to 0 over buildup_s seconds.

Used at the end of a song or between sections. Sets Master only;
underlying RGB stays as set by any previous FX's release tail so the
final state is just "lights off, colours preserved".

A 0-second buildup degrades to a hard cut: 1 s minimum window with
master driven straight to 0 on the first tick. For instant blackout
prefer a `stop` cue (fx_id=0) instead.

params:
    [0] start_master  (0..255; default 255)
    [1] reserved
    [2] reserved
    [3] reserved
    [4] reserved
    [5] reserved
"""

from ..base import Fx, set_ch
from ..channels import block_channel, clamp_group, CH_MASTER
from ..registry import fx_registry


@fx_registry.register
class FadeToBlack(Fx):
    id = 41
    name = "Fade To Black"
    cue_name = "fade_to_black"
    category = "transition"
    description = (
        "Ramps Master from start value to 0 over buildup_s seconds. "
        "Leaves RGB channels alone so the final state is just 'lights "
        "off, colours preserved'. For instant blackout use the `stop` "
        "cue instead."
    )

    PARAMS = [
        ("start_master", "u8",    "Master at start of fade. Default 255."),
        ("group",        "count", "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._start_master = params[0] if params[0] != 0 else 255
        self._group = clamp_group(params[1] if len(params) > 1 else 0)
        dur_s = buildup_s if buildup_s > 0 else 1
        self.default_duration_ms = dur_s * 1000
        self._dur_ms = self.default_duration_ms

    def tick(self, now_ms, universe):
        elapsed = now_ms - self._started_ms
        if elapsed < 0:
            elapsed = 0
        progress = elapsed / self._dur_ms
        if progress > 1.0:
            progress = 1.0
        master = int(self._start_master * (1.0 - progress))
        set_ch(universe, block_channel(self._group, CH_MASTER), master)
