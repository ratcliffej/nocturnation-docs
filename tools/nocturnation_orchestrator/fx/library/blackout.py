"""Blackout - write zeros to every output channel for one tick.

The `stop` cue is a parser alias for this FX. Use either:

    03:00 stop          # alias, preferred in cue files
    03:00 blackout      # explicit form

Why an FX instead of a "just cancel" sentinel: when the runner goes
idle, the orchestrator stops dispatching DMX entirely. The StickC's
mapper holds its last channel values and the Lume holds its last
LIGHT_WASH until TTL expires. So a bare cancel leaves the lights
glowing on whatever was last set. Blackout fixes this by emitting
one final all-zero universe BEFORE the runner goes idle - the
StickC mapper sees the (R,G,B,intensity) change to zero and emits
LIGHT_WASH(0,0,0,intensity=0) to the fleet, which the Lume renders
as off.

After Blackout's one tick the runner returns to idle and the
orchestrator stops dispatching, exactly as the operator wants for a
clean song boundary.
"""

from ..base import Fx
from ..registry import fx_registry


# Full universe span the StickC reads: broadcast block + 9 groups,
# 40 channels each = 400 channels (universe addresses 1..400).
_ZEROED_CHANNELS = 40 * 10


@fx_registry.register
class Blackout(Fx):
    id = 254
    name = "Blackout"
    cue_name = "blackout"
    category = "transition"
    description = (
        "Writes zero to every output channel for one tick, then "
        "auto-finishes. The `stop` cue is an alias. Used to reset "
        "the fleet to dark at song boundaries (e.g. before a "
        "fade-in from black) or any time the LD wants an immediate "
        "blackout. Covers the broadcast block and all 9 group blocks."
    )

    PARAMS = []

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self._ticked = False

    def tick(self, now_ms, universe):
        for i in range(_ZEROED_CHANNELS):
            universe[i] = 0
        self._ticked = True

    def is_finished(self, now_ms):
        # One tick is enough. The main loop's dispatch check is
        # "pre-tick OR post-tick is_active", so the zero frame this
        # tick wrote DOES reach the wire even though the FX finished
        # mid-tick.
        return self._ticked or self._cancelled_ms is not None
