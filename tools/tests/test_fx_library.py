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
from nocturnation_orchestrator.fx.library.linear_buildup import LinearBuildup
from nocturnation_orchestrator.fx.library.strobe_burst import StrobeBurst
from nocturnation_orchestrator.fx.library.fade_to_black import FadeToBlack


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
        with pytest.raises(ValueError):
            block_channel(0, 21)


# ---------------------------------------------------------------------------
# Library registration
# ---------------------------------------------------------------------------

EXPECTED_IDS = {
    1:  ("Quiet Wash",      "ambient"),
    2:  ("Drift Wash",      "ambient"),
    11: ("Sparkle On Beat", "beat"),
    12: ("Pulse Per Bar",   "beat"),
    13: ("Group Cascade",   "beat"),
    21: ("Linear Buildup",  "buildup"),
    32: ("Strobe Burst",    "drop"),
    41: ("Fade To Black",   "transition"),
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


# ---------------------------------------------------------------------------
# DriftWash
# ---------------------------------------------------------------------------

class TestDriftWash:
    def test_writes_both_anchors_and_cycle(self):
        fx = _make(DriftWash, params=(255, 0, 0, 0, 0, 50))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_A_R) == 255
        assert _ch(u, CH_WASH_A_G) == 0
        assert _ch(u, CH_WASH_B_R) == 0
        assert _ch(u, CH_WASH_B_G) == 0
        assert _ch(u, CH_WASH_CYCLE) == 50

    def test_default_cycle_when_zero(self):
        fx = _make(DriftWash, params=(255, 0, 0, 0, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_WASH_CYCLE) == 80


# ---------------------------------------------------------------------------
# SparkleOnBeat
# ---------------------------------------------------------------------------

class TestSparkleOnBeat:
    def test_fires_trigger_high_on_beat(self):
        fx = _make(SparkleOnBeat, bpm=120, params=(200, 0, 100, 200, 0, 0))
        u = _u()
        # First tick of the first beat - rising edge.
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_TRIG) == TRIGGER_HI
        # Same beat, next tick - re-armed low.
        u = _u()
        fx.tick(now_ms=20, universe=u)
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
        fx = _make(SparkleOnBeat, params=(80, 200, 200, 200, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_R) == 80
        assert _ch(u, CH_PULSE_G) == 200
        assert _ch(u, CH_PULSE_B) == 200
        assert _ch(u, CH_PULSE_PROB) == 200

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
        fx = _make(LinearBuildup, buildup_s=4, params=(255, 0, 0, 200, 0, 0))
        u = _u()
        fx.tick(now_ms=0, universe=u)
        assert _ch(u, CH_PULSE_PROB) == 0
        u = _u()
        fx.tick(now_ms=4000, universe=u)
        assert _ch(u, CH_PULSE_PROB) == 200

    def test_clamps_past_duration(self):
        # If the runner over-ticks beyond default_duration_ms, master
        # must not exceed 255.
        fx = _make(LinearBuildup, buildup_s=2, params=(255, 0, 0, 200, 64, 0))
        u = _u()
        fx.tick(now_ms=5000, universe=u)  # well past
        assert _ch(u, CH_MASTER) == 255
        assert _ch(u, CH_PULSE_PROB) == 200

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
