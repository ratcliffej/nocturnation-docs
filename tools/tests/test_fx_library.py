"""FX library smoke tests (Epic 10 B3).

Each test exercises an FX directly (instantiate, start, tick) and
checks the channels it claims to write. The library is also imported
to confirm all FX self-register with the canonical fx_registry.

Tests don't aim for pixel-perfect numeric fidelity of envelopes - the
real test surface is the bench. They DO catch:
- registration drift (id collisions, missing entries)
- channel-layout mistakes (wrong constant used)
- start/tick contract regressions
- progress-clamping bugs (e.g. master overshooting 255 past the
  duration window)
"""

import pytest

from nocturnation_orchestrator.fx.base import UNIVERSE_SIZE
from nocturnation_orchestrator.fx.channels import (
    CH_MASTER, CH_STROBE,
    CH_PULSE_R, CH_PULSE_G, CH_PULSE_B,
    CH_PULSE_TRIG, CH_PULSE_ATK, CH_PULSE_SUS, CH_PULSE_REL, CH_PULSE_PROB,
    CH_WASH_A_R, CH_WASH_A_G, CH_WASH_A_B,
    CH_WASH_B_R, CH_WASH_B_G, CH_WASH_B_B,
    CH_WASH_CYCLE, CH_WASH_INT, CH_WASH_ATK, CH_WASH_REL,
    CH_WASH_PULSE_RESPONSE,
    block_channel, BLOCK_WIDTH, NUM_BLOCKS,
    TRIGGER_HI, TRIGGER_LO,
)
from nocturnation_orchestrator.fx import library  # noqa: F401  side-effects
from nocturnation_orchestrator.fx.registry import fx_registry
from nocturnation_orchestrator.fx.library.quiet_wash import QuietWash
from nocturnation_orchestrator.fx.library.drift_wash import DriftWash
from nocturnation_orchestrator.fx.library.sparkle_on_beat import SparkleOnBeat
from nocturnation_orchestrator.fx.library.pulse_per_bar import PulsePerBar
from nocturnation_orchestrator.fx.library.group_cascade import GroupCascade
from nocturnation_orchestrator.fx.library.wash_with_sparkle import WashWithSparkle
from nocturnation_orchestrator.fx.library.linear_buildup import LinearBuildup
from nocturnation_orchestrator.fx.library.strobe_burst import StrobeBurst
from nocturnation_orchestrator.fx.library.fade_to_black import FadeToBlack
from nocturnation_orchestrator.fx.library.blackout import Blackout
from nocturnation_orchestrator.fx.library.pulse import Pulse


def _u():
    return bytearray(UNIVERSE_SIZE)


def _ch(universe, channel):
    """Read a 1-indexed channel from the universe."""
    return universe[channel - 1]


def _make(cls, **start_kwargs):
    """Build + start an FX with sensible defaults; returns the instance."""
    defaults = dict(
        bpm=120, buildup_s=0,
        params=(0, 0, 0, 0, 0, 0),
        position_ms=0, now_ms=0,
    )
    defaults.update(start_kwargs)
    fx = cls()
    fx.start(**defaults)
    return fx


# ---------------------------------------------------------------------------
# Channel constants
# ---------------------------------------------------------------------------

class TestChannelConstants:
    def test_block_geometry(self):
        # Broadcast block is universe ch 1..40, group 1 is 41..80, etc.
        assert block_channel(0, 1) == 1
        assert block_channel(0, 20) == 20
        assert block_channel(1, 1) == 41
        assert block_channel(1, 20) == 60
        assert block_channel(9, 20) == 9 * BLOCK_WIDTH + 20

    def test_block_channel_validates(self):
        with pytest.raises(ValueError):
            block_channel(NUM_BLOCKS, 1)
        with pytest.raises(ValueError):
            block_channel(0, 0)


class TestPercentToDmx:
    """Pins the percent -> DMX conversion the FX probability params use.
    The StickC mapper buckets the resulting DMX into a pixmob::Chance
    enum (8 slots between CHANCE_4 and CHANCE_100); we don't model the
    bucketing here, but the boundary table is documented in the helper's
    docstring."""

    def test_zero_percent_is_zero_dmx(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        # 0% lands on CHANCE_4 (4% effective floor) after the mapper
        # quantises - the bracelet's wire format has no "never" slot.
        assert percent_to_dmx(0) == 0

    def test_hundred_percent_is_max_dmx(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        # 100% must land in the CHANCE_100 bucket (DMX 224..255).
        assert percent_to_dmx(100) == 255

    def test_fifty_percent_lands_in_chance_50_bucket(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        # 50% -> DMX 128, mapper buckets 128..159 into CHANCE_50.
        assert percent_to_dmx(50) == 128

    def test_clamps_above_100(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        assert percent_to_dmx(150) == 255

    def test_clamps_below_0(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        assert percent_to_dmx(-5) == 0

    def test_none_is_zero(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        assert percent_to_dmx(None) == 0

    def test_intermediate_values_round(self):
        from nocturnation_orchestrator.fx.channels import percent_to_dmx
        # 25% * 2.55 = 63.75 -> rounds to 64
        assert percent_to_dmx(25) == 64
        # 75% * 2.55 = 191.25 -> rounds to 191
        assert percent_to_dmx(75) == 191

    def test_block_channel_rejects_past_active(self):
        # Channel index just past ACTIVE_CHANNELS_PER_BLOCK must raise.
        # The active range grew to 27 with the raw-RGB path (24-27);
        # this test pins block_channel's gate to whatever the current
        # active count is.
        from nocturnation_orchestrator.fx.channels import (
            ACTIVE_CHANNELS_PER_BLOCK,
        )
        with pytest.raises(ValueError):
            block_channel(0, ACTIVE_CHANNELS_PER_BLOCK + 1)


# ---------------------------------------------------------------------------
# Library registration
# ---------------------------------------------------------------------------

EXPECTED_IDS = {
    1:   ("Quiet Wash",        "ambient"),
    2:   ("Drift Wash",        "ambient"),
    11:  ("Sparkle On Beat",   "beat"),
    12:  ("Pulse Per Bar",     "beat"),
    13:  ("Group Cascade",     "beat"),
    14:  ("Wash With Sparkle", "beat"),
    15:  ("Pulse",             "accent"),
    21:  ("Linear Buildup",    "buildup"),
    32:  ("Strobe Burst",      "drop"),
    41:  ("Fade To Black",     "transition"),
    254: ("Blackout",          "transition"),
}


class TestLibraryRegistration:
    def test_all_expected_ids_registered(self):
        for fx_id in EXPECTED_IDS:
            cls = fx_registry.get(fx_id)
            assert cls is not None, "fx_id %d missing from registry" % fx_id

    def test_metadata_matches(self):
        for fx_id, (name, category) in EXPECTED_IDS.items():
            cls = fx_registry.get(fx_id)
            assert cls.name == name
            assert cls.category == category

    def test_no_extra_ids(self):
        registered = set(fx_registry.all_ids())
        expected = set(EXPECTED_IDS)
        unexpected = registered - expected
        assert not unexpected, "unexpected fx_ids registered: %r" % unexpected


# ---------------------------------------------------------------------------
# QuietWash
# ---------------------------------------------------------------------------

class TestQuietWash:
    def test_writes_wash_a_rgb_and_holds(self):
        fx = _make(QuietWash, params=(40, 0, 200, 220, 0, 0))
        u = _u()
        fx.tick(now_ms=10, universe=u)
        assert _ch(u, CH_WASH_A_R) == 40
        assert _ch(u, CH_WASH_A_G) == 0
        assert _ch(u, CH_WASH_A_B) == 200
        # Wash B is zeroed - hold A only.
        assert _ch(u, CH_WASH_B_R) == 0
        assert _ch(u, CH_WASH_B_G) == 0
        assert _ch(u, CH_WASH_B_B) == 0
        assert _ch(u, CH_WASH_CYCLE) == 0
        assert _ch(u, CH_WASH_INT) == 220
        assert _ch(u, CH_MASTER) == 255

    def test_defaults_when_intensity_master_zero(self):
        fx = _make(QuietWash, params=(0, 80, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_INT) == 200
        assert _ch(u, CH_MASTER) == 255

    def test_group_routes_to_correct_block(self):
        # Group 3: writes hit block 3 (universe ch 121..160), broadcast
        # block (ch 1..40) stays untouched.
        fx = _make(QuietWash, params=(100, 100, 100, 0, 0, 3))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        # Group 3 master at universe ch 121.
        assert _ch(u, block_channel(3, CH_MASTER)) == 255
        assert _ch(u, block_channel(3, CH_WASH_A_R)) == 100
        # Broadcast block untouched.
        assert _ch(u, CH_MASTER) == 0
        assert _ch(u, CH_WASH_A_R) == 0

    def test_group_zero_routes_to_broadcast(self):
        # The default (group=0) writes to the broadcast block.
        fx = _make(QuietWash, params=(100, 100, 100, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_MASTER) == 255
        assert _ch(u, CH_WASH_A_R) == 100

    def test_group_param_omitted_in_cue_defaults_to_broadcast(self):
        # The cue parser always builds the params tuple to len(PARAMS),
        # so an omitted group token in the cue file becomes 0 in slot 5.
        from nocturnation_orchestrator.cues import parse_cues
        f = parse_cues("00:00 quiet_wash 100 100 100")
        assert f.cues[0].params == (100, 100, 100, 0, 0, 0)

    def test_writes_pulse_response_so_pulses_overlay(self):
        # Lume's perimeter renderer drops LIGHT_PULSE during wash
        # ATTACK / HOLD unless pulse_response > 0 on the wire (>=128
        # at the mapper). Every wash FX must write CH_WASH_PULSE_RESPONSE
        # or accent pulses are silently dropped.
        fx = _make(QuietWash, params=(40, 0, 200, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_PULSE_RESPONSE) >= 128


# ---------------------------------------------------------------------------
# DriftWash
# ---------------------------------------------------------------------------

class TestDriftWash:
    def test_writes_full_rgb_on_both_anchors_and_cycle(self):
        # a_r, a_g, a_b, b_r, b_g, b_b, cycle
        fx = _make(DriftWash, params=(255, 100, 0, 0, 50, 200, 50))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_A_R) == 255
        assert _ch(u, CH_WASH_A_G) == 100
        assert _ch(u, CH_WASH_A_B) == 0
        assert _ch(u, CH_WASH_B_R) == 0
        assert _ch(u, CH_WASH_B_G) == 50
        assert _ch(u, CH_WASH_B_B) == 200
        assert _ch(u, CH_WASH_CYCLE) == 50

    def test_default_cycle_when_zero(self):
        fx = _make(DriftWash, params=(255, 0, 0, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_CYCLE) == 80

    def test_writes_pulse_response_so_pulses_overlay(self):
        fx = _make(DriftWash, params=(255, 0, 0, 0, 0, 255, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_PULSE_RESPONSE) >= 128


# ---------------------------------------------------------------------------
# SparkleOnBeat
# ---------------------------------------------------------------------------

class TestSparkleOnBeat:
    def test_fires_trigger_high_on_beat(self):
        # 2026-06-28 bench-bug workaround: HI is now held for 100 ms
        # to survive the StickC bridge's last-wins poll. Re-arm only
        # after the hold window expires.
        fx = _make(SparkleOnBeat, bpm=120, params=(200, 0, 100, 200, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Within the hold window - still HI.
        u = _u()
        fx.tick(now_ms=20, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Past the 100 ms hold window - re-armed LO.
        u = _u()
        fx.tick(now_ms=150, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO

    def test_fires_again_on_next_beat(self):
        fx = _make(SparkleOnBeat, bpm=120)
        u = _u()
        fx.tick(now_ms=0, universe=u)         # beat 0
        fx.tick(now_ms=300, universe=u)        # mid-beat
        # 60_000 / 120 = 500 ms per beat. Next beat at 500 ms.
        u2 = _u()
        fx.tick(now_ms=520, universe=u2)
        assert _ch(u2, CH_PULSE_TRIG) == TRIGGER_HI

    def test_writes_rgb_and_prob(self):
        # probability=50 (percent) -> percent_to_dmx(50) = 128 (DMX).
        # The mapper buckets DMX 128 into CHANCE_50 = 50% on the wire,
        # which is what a cue-file author writing "50" intends.
        fx = _make(SparkleOnBeat, params=(80, 200, 200, 50, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_R) == 80
        assert _ch(u, CH_PULSE_G) == 200
        assert _ch(u, CH_PULSE_B) == 200
        assert _ch(u, CH_PULSE_PROB) == 128

    def test_late_join_phase_preserved(self):
        # Started 1500 ms into the song; first beat (relative to song)
        # is at 0, 500, 1000, 1500, ... so position_ms=1500 should be
        # ON a beat.
        fx = _make(SparkleOnBeat, bpm=120, position_ms=1500, now_ms=10_000)
        u = _u()
        fx.tick(now_ms=10_000, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_white_default_when_rgb_zero(self):
        fx = _make(SparkleOnBeat, params=(0, 0, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_R) == 255
        assert _ch(u, CH_PULSE_G) == 255
        assert _ch(u, CH_PULSE_B) == 255


# ---------------------------------------------------------------------------
# PulsePerBar
# ---------------------------------------------------------------------------

class TestPulsePerBar:
    def test_fires_once_per_4_beats_by_default(self):
        fx = _make(PulsePerBar, bpm=120)  # bar = 2000 ms
        u = _u()
        fx.tick(now_ms=0, universe=u)                # bar 0 start
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        fx.tick(now_ms=500, universe=_u())            # still bar 0
        u2 = _u()
        fx.tick(now_ms=1999, universe=u2)
        assert _ch(u2, CH_PULSE_TRIG) == TRIGGER_LO
        u3 = _u()
        fx.tick(now_ms=2010, universe=u3)             # bar 1 starts
        assert _ch(u3, CH_PULSE_TRIG) == TRIGGER_HI


# ---------------------------------------------------------------------------
# WashWithSparkle
# ---------------------------------------------------------------------------

class TestWashWithSparkle:
    def test_writes_both_wash_and_sparkle_channels(self):
        # a_r, a_g, a_b, b_r, b_g, b_b, cycle, s_r, s_g, s_b, prob_u8.
        # Tests bypass the cue parser, so prob is passed as raw u8
        # (255 = 100% chance); the cue parser handles the percent ->
        # u8 conversion elsewhere.
        fx = _make(
            WashWithSparkle, bpm=120,
            params=(255, 0, 255, 0, 0, 255, 10, 255, 255, 255, 255),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        # Wash channels
        assert _ch(u, CH_WASH_A_R) == 255
        assert _ch(u, CH_WASH_A_B) == 255
        assert _ch(u, CH_WASH_B_B) == 255
        assert _ch(u, CH_WASH_CYCLE) == 10
        # Pulse channels (sparkle) - on-beat trigger HIGH on first tick.
        assert _ch(u, CH_PULSE_R) == 255
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        assert _ch(u, CH_PULSE_PROB) == 255

    def test_writes_pulse_response_so_pulse_cues_overlay(self):
        fx = _make(
            WashWithSparkle, bpm=120,
            params=(50, 0, 50, 100, 0, 100, 50, 25, 25, 25, 50),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_PULSE_RESPONSE) >= 128

    def test_sparkle_re_arms_after_hold_window(self):
        # 2026-06-28 bench-bug workaround: HI is now held for HOLD_MS
        # (100 ms) so the StickC bridge's last-wins poll catches it.
        # Trigger drops to LO only AFTER the hold window expires.
        fx = _make(
            WashWithSparkle, bpm=120,
            params=(100, 100, 100, 50, 50, 50, 80, 255, 0, 0, 255),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        # Within the hold window - still HI.
        u = _u()
        fx.tick(now_ms=20, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Past the hold window - re-armed LO.
        u = _u()
        fx.tick(now_ms=150, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        # Wash channels still set throughout.
        assert _ch(u, CH_WASH_A_R) == 100

    def test_sparkle_fires_again_on_next_beat(self):
        fx = _make(
            WashWithSparkle, bpm=120,
            params=(0, 0, 0, 0, 0, 0, 80, 100, 200, 50, 255),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)        # beat 0
        fx.tick(now_ms=300, universe=u)       # mid-beat
        u = _u()
        fx.tick(now_ms=520, universe=u)       # beat 1 (60_000/120=500)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_white_sparkle_default_when_rgb_zero(self):
        # All-zero sparkle RGB defaults to white so an undermade cue
        # still produces visible beat output. Tick 0 is the first
        # beat -> on_beat=true -> sparkle writes pulse RGB at default
        # white. (Epic 11 B0 rollback restored this behaviour after
        # the mid-Epic-11 PixMob refresh layer was removed - the
        # refresh used to win tick 0 over the sparkle.)
        fx = _make(
            WashWithSparkle, bpm=120,
            params=(100, 0, 0, 0, 0, 100, 80, 0, 0, 0, 255),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_R) == 255
        assert _ch(u, CH_PULSE_G) == 255
        assert _ch(u, CH_PULSE_B) == 255


# ---------------------------------------------------------------------------
# GroupCascade
# ---------------------------------------------------------------------------

class TestGroupCascade:
    def test_only_active_group_trigger_fires(self):
        fx = _make(GroupCascade, bpm=120, params=(255, 0, 0, 200, 4, 0))
        u = _u()
        # Beat 0 -> group 1 fires.
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, block_channel(1, CH_PULSE_TRIG)) == TRIGGER_HI
        assert _ch(u, block_channel(2, CH_PULSE_TRIG)) == TRIGGER_LO
        assert _ch(u, block_channel(3, CH_PULSE_TRIG)) == TRIGGER_LO
        assert _ch(u, block_channel(4, CH_PULSE_TRIG)) == TRIGGER_LO
        # Broadcast block untouched.
        assert _ch(u, CH_PULSE_TRIG) == 0

    def test_cycles_through_groups(self):
        fx = _make(GroupCascade, bpm=120, params=(255, 0, 0, 200, 3, 0))
        # Beat 0 -> g1, beat 1 -> g2, beat 2 -> g3, beat 3 -> g1.
        u = _u()
        fx.tick(now_ms=0, universe=u)
        fired = [
            (g, _ch(u, block_channel(g, CH_PULSE_TRIG)))
            for g in range(1, 4)
        ]
        assert fired == [(1, TRIGGER_HI), (2, TRIGGER_LO), (3, TRIGGER_LO)]

        u = _u()
        fx.tick(now_ms=520, universe=u)
        fired = [
            (g, _ch(u, block_channel(g, CH_PULSE_TRIG)))
            for g in range(1, 4)
        ]
        assert fired == [(1, TRIGGER_LO), (2, TRIGGER_HI), (3, TRIGGER_LO)]


# ---------------------------------------------------------------------------
# LinearBuildup
# ---------------------------------------------------------------------------

class TestLinearBuildup:
    def test_master_ramps_from_start_to_full(self):
        fx = _make(
            LinearBuildup,
            bpm=120, buildup_s=4,
            params=(255, 0, 0, 255, 50, 0),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_MASTER) == 50  # start
        u = _u()
        fx.tick(now_ms=2000, universe=u)  # halfway
        mid = _ch(u, CH_MASTER)
        assert 140 <= mid <= 170          # ~152
        u = _u()
        fx.tick(now_ms=4000, universe=u)  # end
        assert _ch(u, CH_MASTER) == 255

    def test_probability_ramps_zero_to_target(self):
        # target_probability=80 (percent) -> percent_to_dmx(80) = 204 (DMX).
        # tick ramps 0 -> 204 across the buildup window.
        fx = _make(LinearBuildup, buildup_s=4, params=(255, 0, 0, 80, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_PROB) == 0
        u = _u()
        fx.tick(now_ms=4000, universe=u)
        assert _ch(u, CH_PULSE_PROB) == 204

    def test_clamps_past_duration(self):
        # If the runner over-ticks beyond default_duration_ms, master
        # must not exceed 255. target_probability=80% holds at 204 DMX.
        fx = _make(LinearBuildup, buildup_s=2, params=(255, 0, 0, 80, 64, 0))
        u = _u()
        fx.tick(now_ms=5000, universe=u)  # well past
        assert _ch(u, CH_MASTER) == 255
        assert _ch(u, CH_PULSE_PROB) == 204

    def test_finished_after_buildup(self):
        fx = _make(LinearBuildup, buildup_s=2)
        assert not fx.is_finished(now_ms=1500)
        assert fx.is_finished(now_ms=2500)


# ---------------------------------------------------------------------------
# StrobeBurst
# ---------------------------------------------------------------------------

class TestStrobeBurst:
    def test_writes_master_and_strobe(self):
        fx = _make(StrobeBurst, params=(5, 200, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_MASTER) == 255
        assert _ch(u, CH_STROBE) == 200

    def test_default_strobe_rate(self):
        fx = _make(StrobeBurst, params=(5, 0, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_STROBE) == 255

    def test_finishes_after_duration(self):
        fx = _make(StrobeBurst, params=(5, 200, 0, 0, 0, 0))
        # 5 * 100 = 500 ms.
        assert not fx.is_finished(now_ms=400)
        assert fx.is_finished(now_ms=600)


# ---------------------------------------------------------------------------
# FadeToBlack
# ---------------------------------------------------------------------------

class TestFadeToBlack:
    def test_master_drops_from_start_to_zero(self):
        fx = _make(FadeToBlack, buildup_s=4, params=(255, 0, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_MASTER) == 255
        u = _u()
        fx.tick(now_ms=2000, universe=u)  # halfway
        mid = _ch(u, CH_MASTER)
        assert 120 <= mid <= 135
        u = _u()
        fx.tick(now_ms=4000, universe=u)  # end
        assert _ch(u, CH_MASTER) == 0

    def test_finishes_after_buildup(self):
        fx = _make(FadeToBlack, buildup_s=2)
        assert not fx.is_finished(now_ms=1500)
        assert fx.is_finished(now_ms=2500)


# ---------------------------------------------------------------------------
# Blackout
# ---------------------------------------------------------------------------

class TestBlackout:
    def test_writes_zero_to_broadcast_block(self):
        # Pre-fill some channels to simulate a wash being interrupted.
        u = _u()
        u[0] = 255       # ch 1 master
        u[10] = 200      # ch 11 wash A R
        u[12] = 200      # ch 13 wash A B
        u[5] = 255       # ch 6 pulse trigger
        fx = _make(Blackout)
        fx.tick(now_ms=0, universe=u)
        # Every broadcast-block channel should be zero now EXCEPT
        # pulse_response (ch 23), which Blackout sets to 255 so the
        # all-zero wash it emits doesn't lock the Lume out of future
        # pulse cues. See blackout.py for rationale.
        from nocturnation_orchestrator.fx.channels import CH_WASH_PULSE_RESPONSE
        for ch in range(1, 41):
            if ch == CH_WASH_PULSE_RESPONSE:
                continue
            assert u[ch - 1] == 0, "ch %d should be zero, got %d" % (ch, u[ch - 1])
        assert u[CH_WASH_PULSE_RESPONSE - 1] == 255

    def test_writes_zero_to_group_blocks_too(self):
        # Pre-fill channels in groups 1..9 (broadcast = block 0;
        # group 1 starts at offset 40, etc.).
        u = _u()
        for block in range(10):
            u[block * 40] = 255  # master of each block
        fx = _make(Blackout)
        fx.tick(now_ms=0, universe=u)
        for block in range(10):
            assert u[block * 40] == 0

    def test_keeps_pulse_response_open_on_every_block(self):
        # Blackout's purpose is "lights off NOW, but don't block what
        # comes next". Every block's pulse_response channel must come
        # out of Blackout at 255 so the all-zero wash the StickC mapper
        # forwards to the Lume permits subsequent pulse cues.
        from nocturnation_orchestrator.fx.channels import (
            CH_WASH_PULSE_RESPONSE, block_channel,
        )
        u = _u()
        fx = _make(Blackout)
        fx.tick(now_ms=0, universe=u)
        for block in range(10):
            assert u[block_channel(block, CH_WASH_PULSE_RESPONSE) - 1] == 255

    def test_finishes_after_one_tick(self):
        fx = _make(Blackout)
        assert not fx.is_finished(now_ms=0)
        u = _u()
        fx.tick(now_ms=10, universe=u)
        assert fx.is_finished(now_ms=20)

    def test_stop_cue_resolves_to_blackout(self):
        # The `stop` alias in cue files lands on the Blackout FX.
        from nocturnation_orchestrator.cues import parse_cues
        f = parse_cues("03:00 stop")
        assert f.cues[0].fx_id == Blackout.id


# ---------------------------------------------------------------------------
# Pulse (one-shot)
# ---------------------------------------------------------------------------

class TestPulse:
    def test_tick0_primes_low_then_tick1_fires_high(self):
        # Two-tick fire sequence so the StickC mapper always sees a
        # clean rising edge even if the previous FX left trig HIGH.
        fx = _make(
            Pulse,
            # r, g, b, attack, sustain, decay, probability, group
            #                                       (1/10 s units)
            params=(255, 100, 0, 0, 1, 5, 255, 0),
        )
        # Tick 0: trig=LOW (prime).
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        assert _ch(u, CH_PULSE_R) == 255
        # Tick 1: trig=HIGH (rising edge -> mapper fires).
        u = _u()
        fx.tick(now_ms=20, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_finishes_after_two_ticks(self):
        fx = _make(Pulse, params=(255, 0, 0, 0, 1, 5, 0, 0))
        assert not fx.is_finished(now_ms=0)
        fx.tick(now_ms=0, universe=_u())
        assert not fx.is_finished(now_ms=10)
        fx.tick(now_ms=20, universe=_u())
        assert fx.is_finished(now_ms=30)

    def test_white_default_when_rgb_zero(self):
        fx = _make(Pulse, params=(0, 0, 0, 0, 1, 5, 100, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_R) == 255
        assert _ch(u, CH_PULSE_G) == 255
        assert _ch(u, CH_PULSE_B) == 255

    def test_group_routing(self):
        fx = _make(Pulse, params=(255, 0, 0, 0, 1, 5, 255, 4))
        u = _u()
        fx.tick(now_ms=0, universe=u)   # tick 0 (LOW)
        u = _u()
        fx.tick(now_ms=20, universe=u)  # tick 1 (HIGH)
        # Group 4 trigger fires; broadcast block untouched.
        from nocturnation_orchestrator.fx.channels import block_channel
        assert _ch(u, block_channel(4, CH_PULSE_TRIG)) == TRIGGER_HI
        assert _ch(u, CH_PULSE_TRIG) == 0

    def test_pixmob_time_quantisation_in_cue(self):
        # 1/10 s units in the cue file get mapped to pixmob::Time
        # buckets via the slider value. 5 (= 500 ms) is closest to
        # T_480_MS (bucket 4 -> slider 128); 1 (= 100 ms) lands in
        # T_96_MS (bucket 2 -> slider 64).
        from nocturnation_orchestrator.cues import parse_cues
        f = parse_cues("00:30 pulse 255 0 0 1 0 5 100 0")
        c = f.cues[0]
        assert c.fx_id == Pulse.id
        # params: (r, g, b, attack, sustain, decay, prob_u8, group)
        # attack=1 -> 100 ms -> T_96_MS bucket -> slider 64
        # sustain=0 -> 0 ms -> T_0_MS -> slider 0
        # decay=5 -> 500 ms -> T_480_MS bucket -> slider 128
        # prob=100% -> 255
        assert c.params[3] == 64
        assert c.params[4] == 0
        assert c.params[5] == 128
        assert c.params[6] == 255


# ---------------------------------------------------------------------------
# Epic 14.9 Block B: runtime beats[] consumption
# ---------------------------------------------------------------------------

def _make_with_beats(cls, beats_ms, **start_kwargs):
    """Build + start an FX with a beats list attached the way the
    runner does it (set the attribute, THEN call start)."""
    defaults = dict(
        bpm=120, buildup_s=0,
        params=(0, 0, 0, 0, 0, 0),
        position_ms=0, now_ms=0,
    )
    defaults.update(start_kwargs)
    fx = cls()
    fx.beats_ms = beats_ms
    fx.start(**defaults)
    return fx


class TestSparkleOnBeatGridSync:
    """SparkleOnBeat with the runner's beats[] attached. The FX should
    fire on the actual MIR beat positions rather than a regular
    bpm-derived clock. Useful when the song has a pickup silence
    (Viva La Vida: first beat at ~1.21 s) or rubato (where the bpm
    clock would drift relative to the music)."""

    def test_no_fire_before_first_beat(self):
        # beats[0] = 1210ms; nothing should fire at song time 0..1209.
        fx = _make_with_beats(SparkleOnBeat, [1210, 1640, 2070, 2500])
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        u = _u()
        fx.tick(now_ms=1209, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO

    def test_fires_exactly_on_first_beat(self):
        fx = _make_with_beats(SparkleOnBeat, [1210, 1640, 2070, 2500])
        u = _u()
        fx.tick(now_ms=1210, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_fires_on_each_subsequent_beat(self):
        fx = _make_with_beats(SparkleOnBeat, [1210, 1640, 2070, 2500])
        fx.tick(now_ms=1210, universe=_u())   # beat 0
        u = _u()
        # 1500ms is between beat 0 and beat 1 - no fire.
        fx.tick(now_ms=1500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        u = _u()
        fx.tick(now_ms=1640, universe=u)   # beat 1 - rising edge
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        u = _u()
        fx.tick(now_ms=2070, universe=u)   # beat 2
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_late_join_skips_past_beats(self):
        # Started 2 seconds into a song with beats at 0, 500, 1000,
        # 1500, 2000, 2500. The FX shouldn't retro-fire beats 0..3 on
        # its first tick; only beats 4+ matter going forward.
        fx = _make_with_beats(
            SparkleOnBeat, [0, 500, 1000, 1500, 2000, 2500],
            position_ms=2000, now_ms=10_000,
        )
        u = _u()
        # First tick at the same wall-clock time - we're AT beat 4
        # right now; treat that as an already-counted boundary.
        fx.tick(now_ms=10_000, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        u = _u()
        # 500ms later: song-time 2500 - that's beat 5, fire.
        fx.tick(now_ms=10_500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_falls_back_to_bpm_clock_without_beats(self):
        # Empty beats list: the FX behaves as it did pre-14.9.
        fx = _make_with_beats(SparkleOnBeat, [], bpm=120)
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        u = _u()
        fx.tick(now_ms=500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI


class TestPulsePerBarGridSync:
    """PulsePerBar with beats[] attached. Bar 1 = beats[0]; bar 2 =
    beats[time_sig]; etc. (4/4 default, override via the
    beats_per_bar param.)"""

    def _beat_grid(self, count, period_ms=500, anchor_ms=1210):
        """Synthetic 120-BPM beat grid starting at anchor_ms."""
        return [anchor_ms + i * period_ms for i in range(count)]

    def test_fires_on_first_downbeat(self):
        beats = self._beat_grid(16)   # 16 beats, 4 bars of 4/4
        fx = _make_with_beats(PulsePerBar, beats)
        # First bar boundary lands on beats[0].
        u = _u()
        fx.tick(now_ms=beats[0], universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Beats 1, 2, 3 are within bar 1 - no fire.
        for i in (1, 2, 3):
            u = _u()
            fx.tick(now_ms=beats[i], universe=u)
            assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO

    def test_fires_on_second_downbeat(self):
        beats = self._beat_grid(16)
        fx = _make_with_beats(PulsePerBar, beats)
        fx.tick(now_ms=beats[0], universe=_u())   # bar 1
        u = _u()
        # beats[4] is bar 2 downbeat (4-beat bars).
        fx.tick(now_ms=beats[4], universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_beats_per_bar_param_overrides(self):
        # 3/4 time: bars on beats[0], beats[3], beats[6]...
        beats = self._beat_grid(9)
        fx = _make_with_beats(
            PulsePerBar, beats,
            params=(0, 0, 0, 100, 3, 0),   # beats_per_bar=3
        )
        # beats[0] fires (bar 1).
        u = _u()
        fx.tick(now_ms=beats[0], universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # beats[3] fires (bar 2).
        u = _u()
        fx.tick(now_ms=beats[3], universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # beats[1], beats[2] don't fire.
        for i in (1, 2):
            u = _u()
            # Sequence the FX so it doesn't see beats[3] yet on this
            # iteration; rebuild from scratch for cleanliness.
            fx2 = _make_with_beats(
                PulsePerBar, beats, params=(0, 0, 0, 100, 3, 0),
            )
            fx2.tick(now_ms=beats[0], universe=_u())   # warm up: bar 1 done
            u2 = _u()
            fx2.tick(now_ms=beats[i], universe=u2)
            assert _ch(u2, CH_PULSE_TRIG) == TRIGGER_LO

    def test_falls_back_to_bpm_clock_without_beats(self):
        # Empty beats list -> classic behaviour: 1 pulse per 2000 ms at 120 BPM 4/4.
        fx = _make_with_beats(PulsePerBar, [], bpm=120)
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI


class TestWashWithSparkleGridSync:
    """Epic 14.9 follow-up. wash_with_sparkle missed the original
    Block B pass and stayed on the bpm-clock - which the operator
    noticed when the sparkle phase-offset by ~90 ms relative to the
    actual bass drum (140 declared bpm vs 139.67 librosa-measured).
    Same beat-grid contract as sparkle_on_beat once the runner
    attaches beats_ms."""

    def test_no_fire_before_first_beat(self):
        # beats[0] = 1210ms; nothing should fire at song time 0..1209.
        fx = _make_with_beats(
            WashWithSparkle, [1210, 1640, 2070, 2500],
            params=(50, 50, 50, 100, 100, 100, 80,
                    255, 255, 255, 255),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO

    def test_fires_exactly_on_grid_beats(self):
        beats = [1210, 1640, 2070, 2500]
        fx = _make_with_beats(
            WashWithSparkle, beats,
            params=(50, 50, 50, 100, 100, 100, 80,
                    255, 255, 255, 255),
        )
        # First beat in the grid - rising edge.
        u = _u()
        fx.tick(now_ms=1210, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Past the hold window (1210 + 100 = 1310 ms) but before
        # the next beat at 1640 - re-armed low.
        u = _u()
        fx.tick(now_ms=1500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        # Second beat - rising edge again.
        u = _u()
        fx.tick(now_ms=1640, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_uses_librosa_grid_not_bpm_clock(self):
        # The regression we shipped this fix for: at 140 declared bpm
        # the bpm-clock fires every 428.6 ms anchored to song-time 0,
        # so it would emit a rising edge on the very first tick (t=0).
        # On the librosa grid with beats[0] = 1200 ms, t=0 is BEFORE
        # any beat - so the FX should stay LOW until the grid's first
        # beat is crossed. The "no fire at t=0" assertion is what
        # distinguishes the two modes cleanly.
        beats = [1200, 1630, 2071, 2501]   # uneven 430/441 like librosa
        fx = _make_with_beats(
            WashWithSparkle, beats, bpm=140,
            params=(0, 0, 0, 0, 0, 0, 80,
                    255, 255, 255, 255),
        )
        # Grid mode: no beat at or before 0, so no rising edge yet.
        # bpm-clock mode WOULD have fired here.
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        # Crossing into the grid's first beat fires the trigger.
        u = _u()
        fx.tick(now_ms=1200, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Second grid beat at 1630 - rising edge again.
        u = _u()
        fx.tick(now_ms=1500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO   # mid-beat re-arm
        u = _u()
        fx.tick(now_ms=1630, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_falls_back_to_bpm_clock_without_beats(self):
        # Empty beats list -> classic behaviour: pulse on bpm clock.
        fx = _make_with_beats(
            WashWithSparkle, [], bpm=120,
            params=(50, 50, 50, 100, 100, 100, 80,
                    255, 255, 255, 255),
        )
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI


class TestMusicPositionDrivenBeatSync:
    """Bench-found bug 2026-06-28: FX were ticking off wall-clock-
    elapsed-since-cue-start, not music position. Symptom: badge kept
    pulsing while music was paused. Architectural fix: runner stores
    position_ms on the FX instance before each tick; beat-aware FX
    prefer self.position_ms over the wall-clock anchor math when set."""

    def _make_with_beats_and_pos(self, cls, beats_ms, pos_ms, **start_kwargs):
        defaults = dict(
            bpm=140, buildup_s=0,
            params=(0, 0, 0, 100, 0, 0),
            position_ms=0, now_ms=0,
        )
        defaults.update(start_kwargs)
        fx = cls()
        fx.beats_ms = beats_ms
        fx.position_ms = pos_ms
        fx.start(**defaults)
        return fx

    def test_sparkle_freezes_when_position_doesnt_advance(self):
        # Music paused at song-time 12.945s (right on a beat). Wall
        # clock keeps ticking. The FX should NOT fire on subsequent
        # ticks because the music position doesn't advance.
        beats = [12945, 13375, 13805, 14234]   # 4 successive beats
        fx = self._make_with_beats_and_pos(
            SparkleOnBeat, beats, pos_ms=12945,
            position_ms=12945, now_ms=100000,
            params=(255, 255, 255, 100, 0),
        )
        # First tick at the cue start - we're AT beat 12945 already
        # via pre-seed. No fire.
        u = _u()
        fx.position_ms = 12945
        fx.tick(now_ms=100000, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        # Wall clock advances 1 second; music position FROZEN (paused).
        # The FX must not fire - beat index unchanged because position
        # is unchanged.
        u = _u()
        fx.position_ms = 12945   # still paused
        fx.tick(now_ms=101000, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        # Resume: position advances to next beat. FX fires.
        u = _u()
        fx.position_ms = 13375
        fx.tick(now_ms=101500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_wash_with_sparkle_freezes_when_position_doesnt_advance(self):
        beats = [12945, 13375, 13805, 14234]
        fx = self._make_with_beats_and_pos(
            WashWithSparkle, beats, pos_ms=12945,
            position_ms=12945, now_ms=100000,
            params=(0, 0, 0, 0, 0, 0, 80, 255, 255, 255, 255),
        )
        u = _u()
        fx.position_ms = 12945
        fx.tick(now_ms=100000, universe=u)
        # Just crossed beat 12945; first tick fires.
        # Pause: wall clock advances, position frozen.
        u = _u()
        fx.position_ms = 12945
        fx.tick(now_ms=101000, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO

    def test_pulse_per_bar_freezes_when_position_doesnt_advance(self):
        # 4-beat bar; bars at beats[0] and beats[4].
        beats = [1000 + i * 430 for i in range(10)]
        fx = self._make_with_beats_and_pos(
            PulsePerBar, beats, pos_ms=1000,
            position_ms=1000, now_ms=100000,
            params=(255, 255, 255, 100, 4, 0),
        )
        # Initial tick on bar 1.
        fx.tick(now_ms=100000, universe=_u())
        # Pause: wall clock crosses what would be bar 2 (in bpm-clock),
        # but music position doesn't advance.
        u = _u()
        fx.position_ms = 1000
        fx.tick(now_ms=103000, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_LO
        # Resume to bar 2.
        u = _u()
        fx.position_ms = beats[4]   # bar 2 downbeat
        fx.tick(now_ms=103500, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI

    def test_fallback_when_no_position_ms_set(self):
        # If the runner doesn't set position_ms (back-compat with
        # callers not updated), the FX falls back to wall-clock
        # math. Existing tests cover this path; here we just confirm
        # explicit absence doesn't break anything.
        beats = [1210, 1640, 2070, 2500]
        fx = SparkleOnBeat()
        fx.beats_ms = beats
        # Deliberately do NOT set fx.position_ms.
        fx.start(bpm=120, buildup_s=0,
                 params=(255, 255, 255, 100, 0),
                 position_ms=0, now_ms=0)
        u = _u()
        fx.tick(now_ms=1210, universe=u)
        # Wall-clock fallback path: fires when elapsed crosses beat[0].
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
