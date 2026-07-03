"""Pulse - fires a single pulse at the cue's time and finishes.

Use to accent specific moments (a snare hit, a vocal stab, a section
transition). Unlike `sparkle_on_beat` / `pulse_per_bar` which keep
firing at a beat cadence, `pulse` is one-shot: it writes the rising-
edge trigger sequence then auto-finishes.

Envelope (attack / sustain / decay) takes 1/10 s units and quantises
onto the pixmob::Time 8-bucket lookup the StickC mapper uses (the
nearest of 0, 32, 96, 192, 480, 960, 2400, 3840 ms). "Decay" here is
the wire-side "Release" - same thing, fall-off after sustain.

Two-tick fire sequence:
    tick 0:  trigger LOW  (re-arms the mapper in case the previous
                           FX left it high)
    tick 1:  trigger HIGH (rising edge -> mapper emits LIGHT_PULSE)
    finish.

Universe channels written (per cue.group):
    1 Master                  <- 255
    3..5 Pulse RGB            <- params[0..2]
    6 Pulse Trigger           <- LOW (tick 0), HIGH (tick 1)
    7 Pulse Attack            <- pixmob slider for params[3] 1/10 s
    8 Pulse Sustain           <- pixmob slider for params[4] 1/10 s
    9 Pulse Release (Decay)   <- pixmob slider for params[5] 1/10 s
    10 Pulse Probability      <- params[6] (percent -> u8)
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
class Pulse(Fx):
    id = 15
    name = "Pulse"
    cue_name = "pulse"
    category = "accent"
    description = (
        "Fires one pulse at the cue's time and finishes. For accenting "
        "specific moments (snare hits, vocal stabs, transitions).\n\n"
        "**Attack / Sustain / Decay (ASR) bands.** Each phase takes a "
        "value in **1/10 s units** (so `2` = 0.2 s, `10` = 1.0 s), "
        "then quantises onto the wire-side 8-bucket pixmob::Time enum. "
        "Several adjacent 1/10 s values land in the same bucket; pick "
        "from the table below so you know what you'll actually get:\n\n"
        "| Cue value (1/10 s) | Rendered (ms) | Pixmob bucket | Feel        |\n"
        "|--------------------|---------------|---------------|-------------|\n"
        "| 0                  | 0             | T_0_MS        | snap        |\n"
        "| 1                  | ~96           | T_96_MS       | quick       |\n"
        "| 2, 3               | ~192          | T_192_MS      | gentle      |\n"
        "| 4-7                | ~480          | T_480_MS      | swell       |\n"
        "| 8-16               | ~960          | T_960_MS      | slow swell  |\n"
        "| 17-31              | ~2400         | T_2400_MS     | drone       |\n"
        "| 32+                | ~3840         | T_3840_MS     | long drone  |\n\n"
        "Note T_32_MS is unreachable from the 1/10 s input: value `1` "
        "(100 ms) is closer to the T_96_MS bucket centre than to T_32_MS, "
        "so it rounds up.\n\n"
        "**Worked examples** - the four shapes most cues need:\n\n"
        "```\n"
        "# Snap white flash on a snare hit - instant on, no hold, 0.2 s tail.\n"
        "00:30.5  pulse  255 255 255   0 0 2   100\n"
        "\n"
        "# Drop accent in red - instant on, brief hold, 0.5 s tail.\n"
        "01:14.0  pulse  255 0   0     0 2 5   100\n"
        "\n"
        "# Vocal stab in cyan - quick swell-in, no hold, 0.5 s release.\n"
        "00:45.2  pulse  0   200 255   1 0 5   100\n"
        "\n"
        "# Slow swell in deep purple - 1 s in, 1 s hold, 1 s out.\n"
        "02:10.0  pulse  150 50  200   10 10 10   100\n"
        "```\n\n"
        "Rule of thumb: if you want it to read as a **flash**, keep "
        "attack at 0 (snap on). If you want a **swell**, attack > 0 "
        "and use bright RGB - smooth attack at dim RGB just blurs into "
        "the wash and looks like 'nothing happened'.\n\n"
        "**One-shot, not repeating.** Each cue fires exactly once. For "
        "a per-beat texture use `sparkle_on_beat`; for one-per-bar use "
        "`pulse_per_bar`. Repeating identical `pulse` cues at 1 s "
        "intervals is fine but the bracelet barely settles between "
        "fires - widen the spacing if the pulses are visually merging."
    )

    PARAMS = [
        ("r",           "u8",          "Pulse Red. White default if R/G/B all zero."),
        ("g",           "u8",          "Pulse Green."),
        ("b",           "u8",          "Pulse Blue."),
        ("attack",      "pixmob_time", "Attack time, 1/10 s units. Quantised to the nearest pixmob::Time bucket."),
        ("sustain",     "pixmob_time", "Sustain time, 1/10 s units. Quantised to nearest bucket."),
        ("decay",       "pixmob_time", "Decay (fall-off) time, 1/10 s units. Quantised to nearest bucket."),
        ("probability", "percent",     "Chance the pulse actually fires (0..100%). Default 100%."),
        ("group",       "count",       "Target device group: 0 = all (broadcast), 1..9 = group N. Default 0."),
    ]

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        r, g, b = params[0], params[1], params[2]
        if r == 0 and g == 0 and b == 0:
            r, g, b = 255, 255, 255
        self._r, self._g, self._b = r, g, b
        self._attack = params[3]
        self._sustain = params[4]
        self._decay = params[5]
        # to_u8() in cues.py already ran percent->u8 on this slot
        # (declared "percent" in PARAMS). Take params[6] verbatim;
        # fall back to 255 (== 100%) when the cue omitted the value.
        self._prob = params[6] if params[6] != 0 else 255
        self._group = clamp_group(params[7] if len(params) > 7 else 0)
        self._ticks = 0

    def tick(self, now_ms, universe):
        g = self._group
        set_ch(universe, block_channel(g, CH_MASTER),     255)
        set_ch(universe, block_channel(g, CH_PULSE_R),    self._r)
        set_ch(universe, block_channel(g, CH_PULSE_G),    self._g)
        set_ch(universe, block_channel(g, CH_PULSE_B),    self._b)
        set_ch(universe, block_channel(g, CH_PULSE_ATK),  self._attack)
        set_ch(universe, block_channel(g, CH_PULSE_SUS),  self._sustain)
        set_ch(universe, block_channel(g, CH_PULSE_REL),  self._decay)
        set_ch(universe, block_channel(g, CH_PULSE_PROB), self._prob)
        # Tick 0 primes LOW (re-arm); tick 1 fires HIGH (rising edge).
        if self._ticks == 0:
            set_ch(universe, block_channel(g, CH_PULSE_TRIG), TRIGGER_LO)
        else:
            set_ch(universe, block_channel(g, CH_PULSE_TRIG), TRIGGER_HI)
        self._ticks += 1

    def is_finished(self, now_ms):
        # Two ticks: prime + fire. After that the universe sits with
        # trig=HIGH; the next Pulse cue (or Blackout) will write LOW
        # again to re-arm.
        return self._ticks >= 2 or self._cancelled_ms is not None
