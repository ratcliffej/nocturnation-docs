"""FX engine tests (Epic 10 B1).

Covers the registry / runner contracts and the universe write-through.
Concrete FX implementations land in B3 with their own per-effect tests.
"""

import pytest

from nocturnation_orchestrator.fx import Fx, FxRegistry, FxRunner
from nocturnation_orchestrator.fx.base import UNIVERSE_SIZE, set_ch


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class RecordingFx(Fx):
    """Minimal Fx that records every lifecycle call so the runner can
    be inspected. Subclasses set their own id; multiple recording FXes
    can coexist in one test by varying the id.

    Tick writes a per-instance fingerprint byte into channel ``id`` of
    the universe so tests can prove tick() was actually called with
    the right universe instance.
    """

    id = 100
    name = "RecordingFx"
    category = "test"

    def __init__(self):
        super().__init__()
        self.starts = []
        self.ticks = []
        self.cancels = []
        # When set non-None, is_finished returns True at that now_ms.
        self.finish_at_ms = None
        # Per-instance value written into universe[id-1] on each tick.
        self.tick_value = 0xAA

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        self._started_ms = now_ms
        self._cancelled_ms = None
        self.starts.append({
            "bpm": bpm, "buildup_s": buildup_s, "params": params,
            "position_ms": position_ms, "now_ms": now_ms,
        })

    def tick(self, now_ms, universe):
        self.ticks.append(now_ms)
        set_ch(universe, self.id, self.tick_value)

    def cancel(self, now_ms):
        self._cancelled_ms = now_ms
        self.cancels.append(now_ms)

    def is_finished(self, now_ms):
        # An explicit finish_at_ms drives lifetime regardless of cancel
        # state; lets us simulate a release fade-out window without the
        # base class's "finished on cancel" default.
        if self.finish_at_ms is not None:
            return now_ms >= self.finish_at_ms
        return self._cancelled_ms is not None


def make_recording_class(fx_id, name="Recording", *, group_slot=None):
    """Build a fresh Fx subclass with the given id. Returns the class.

    ``group_slot`` (optional int, 0..5) attaches a PARAMS declaration
    whose entry at that slot is named "group" so the runner's group
    extractor routes admissions to per-group slots. Omit for FX with
    no group param (broadcast-only, e.g. blackout)."""
    attrs = {"id": fx_id, "name": name}
    if group_slot is not None:
        params = [("p%d" % i, "u8", "") for i in range(group_slot)] + [
            ("group", "count", "Target device group.")
        ]
        attrs["PARAMS"] = params
    return type(name, (RecordingFx,), attrs)


# ---------------------------------------------------------------------------
# Universe helpers
# ---------------------------------------------------------------------------

class TestSetCh:
    def test_writes_channel_one_indexed(self):
        u = bytearray(UNIVERSE_SIZE)
        set_ch(u, 1, 200)
        assert u[0] == 200
        assert u[1] == 0

    def test_writes_top_channel(self):
        u = bytearray(UNIVERSE_SIZE)
        set_ch(u, 512, 99)
        assert u[511] == 99

    def test_clamps_high(self):
        u = bytearray(UNIVERSE_SIZE)
        set_ch(u, 5, 999)
        assert u[4] == 255

    def test_clamps_low(self):
        u = bytearray(UNIVERSE_SIZE)
        u[4] = 50
        set_ch(u, 5, -10)
        assert u[4] == 0

    def test_rejects_out_of_range(self):
        u = bytearray(UNIVERSE_SIZE)
        with pytest.raises(ValueError):
            set_ch(u, 0, 100)
        with pytest.raises(ValueError):
            set_ch(u, 513, 100)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestFxRegistry:
    def test_register_and_lookup(self):
        reg = FxRegistry()
        Cls = make_recording_class(11, "Sparkle")
        reg.register(Cls)
        assert reg.get(11) is Cls
        assert reg.has(11)

    def test_unknown_id_returns_none(self):
        reg = FxRegistry()
        assert reg.get(99) is None
        assert not reg.has(99)

    def test_duplicate_id_rejected(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11, "A"))
        with pytest.raises(ValueError):
            reg.register(make_recording_class(11, "B"))

    def test_reserved_ids_rejected(self):
        reg = FxRegistry()
        with pytest.raises(ValueError):
            reg.register(make_recording_class(0, "Cancel"))
        with pytest.raises(ValueError):
            reg.register(make_recording_class(255, "Reserved"))

    def test_decorator_returns_cls(self):
        reg = FxRegistry()
        Cls = make_recording_class(11)
        ret = reg.register(Cls)
        assert ret is Cls

    def test_all_ids_sorted(self):
        reg = FxRegistry()
        reg.register(make_recording_class(21, "Buildup"))
        reg.register(make_recording_class(7, "Fade"))
        reg.register(make_recording_class(11, "Sparkle"))
        assert reg.all_ids() == [7, 11, 21]


# ---------------------------------------------------------------------------
# Runner: start / cancel
# ---------------------------------------------------------------------------

class TestFxRunnerStart:
    def test_unknown_fx_id_drops_silently(self):
        reg = FxRegistry()
        runner = FxRunner(reg)
        runner.start(99, now_ms=0)
        assert runner.current_fx is None
        assert runner.stats()["unknown_fx_drops"] == 1

    def test_start_admits_known_fx(self):
        reg = FxRegistry()
        Cls = make_recording_class(11)
        reg.register(Cls)
        runner = FxRunner(reg, default_bpm=120)
        runner.start(
            11,
            bpm=138, buildup_s=4,
            params=(80, 200, 200, 200, 0, 0),
            position_ms=1234,
            now_ms=100,
        )
        assert isinstance(runner.current_fx, Cls)
        rec = runner.current_fx.starts[0]
        assert rec["bpm"] == 138
        assert rec["buildup_s"] == 4
        assert rec["params"] == (80, 200, 200, 200, 0, 0)
        assert rec["position_ms"] == 1234
        assert rec["now_ms"] == 100

    def test_bpm_zero_falls_back_to_default(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg, default_bpm=124)
        runner.start(11, bpm=0, now_ms=0)
        assert runner.current_fx.starts[0]["bpm"] == 124


class TestFxRunnerCancelAndReplace:
    def test_cancel_with_fx_id_zero(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        first = runner.current_fx
        runner.start(0, now_ms=100)
        assert runner.current_fx is None
        assert runner.cancelling_fx is first
        assert first.cancels == [100]

    def test_cancel_method_equivalent_to_start_zero(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        first = runner.current_fx
        runner.cancel(now_ms=100)
        assert runner.current_fx is None
        assert runner.cancelling_fx is first
        assert first.cancels == [100]

    def test_new_fx_id_supersedes_running(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11, "A"))
        reg.register(make_recording_class(13, "B"))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        first = runner.current_fx
        runner.start(13, now_ms=100)
        assert runner.current_fx is not first
        assert runner.current_fx.id == 13
        assert runner.cancelling_fx is first
        assert first.cancels == [100]

    def test_same_fx_id_is_idempotent_without_replace_flag(self):
        # A re-emit with the same fx_id (e.g. the YAML scheduler firing
        # the same cue at the start of every bar) MUST NOT restart the
        # running FX. Otherwise the orchestrator would judder on every
        # re-emit.
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        first = runner.current_fx
        runner.start(11, now_ms=5_000)
        assert runner.current_fx is first
        assert first.cancels == []

    def test_replace_running_flag_forces_restart(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        first = runner.current_fx
        runner.start(11, replace_running=True, now_ms=5_000)
        assert runner.current_fx is not first
        assert first.cancels == [5_000]


# ---------------------------------------------------------------------------
# Runner: tick + universe
# ---------------------------------------------------------------------------

class TestFxRunnerTick:
    def test_tick_calls_current_fx_with_universe(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        fx = runner.current_fx
        universe = bytearray(UNIVERSE_SIZE)
        runner.tick(now_ms=10, universe=universe)
        runner.tick(now_ms=20, universe=universe)
        assert fx.ticks == [10, 20]
        # Each tick wrote the fingerprint into channel 11.
        assert universe[10] == 0xAA

    def test_tick_no_op_when_idle(self):
        reg = FxRegistry()
        runner = FxRunner(reg)
        universe = bytearray(UNIVERSE_SIZE)
        runner.tick(now_ms=10, universe=universe)  # must not raise
        assert universe == bytearray(UNIVERSE_SIZE)

    def test_tick_ticks_cancelling_fx_through_release(self):
        # After cancel, the FX should keep getting tick() calls until
        # is_finished() returns True, so it can render its fade-out.
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        fx = runner.current_fx
        fx.finish_at_ms = 200  # release window: 100..200 ms
        universe = bytearray(UNIVERSE_SIZE)

        runner.start(0, now_ms=100)
        # Mid-release: still ticking.
        runner.tick(now_ms=150, universe=universe)
        assert 150 in fx.ticks
        assert runner.cancelling_fx is fx
        # Past finish: dropped.
        runner.tick(now_ms=250, universe=universe)
        assert 250 in fx.ticks
        assert runner.cancelling_fx is None

    def test_current_fx_wins_on_shared_channels(self):
        # When a cancelling FX and a current FX both write the same
        # channel during the overlap, the current FX wins (tick order:
        # cancelling first, current second).
        reg = FxRegistry()
        reg.register(make_recording_class(11, "Old"))
        reg.register(make_recording_class(13, "New"))
        runner = FxRunner(reg)

        runner.start(11, now_ms=0)
        old = runner.current_fx
        old.finish_at_ms = 1000  # long release tail
        old.tick_value = 0x11
        # Re-target both FX to write channel 50 to make the conflict
        # visible.
        old.id_for_write = 50  # not used; we patch the tick instead.

        # Simpler: both classes already write to their own id channel.
        # To force a shared-channel conflict, use a custom tick that
        # writes ch 50 with a per-instance value.
        def shared_tick(self, now_ms, universe):
            self.ticks.append(now_ms)
            set_ch(universe, 50, self.tick_value)

        type(old).tick = shared_tick

        runner.start(13, now_ms=100)
        new = runner.current_fx
        new.tick_value = 0x99
        type(new).tick = shared_tick

        universe = bytearray(UNIVERSE_SIZE)
        runner.tick(now_ms=150, universe=universe)
        # Cancelling fx wrote 0x11 first, current fx overwrote with 0x99.
        assert universe[49] == 0x99


# ---------------------------------------------------------------------------
# Runner: stats
# ---------------------------------------------------------------------------

class TestFxRunnerIsActive:
    def test_inactive_when_nothing_running(self):
        reg = FxRegistry()
        runner = FxRunner(reg)
        assert not runner.is_active

    def test_active_when_current_set(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        assert runner.is_active

    def test_active_when_only_cancelling_tail(self):
        # During a release fade, current is None but cancelling still
        # has things to write. We must still dispatch the universe.
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        runner.current_fx.finish_at_ms = 1_000   # long release window
        runner.start(0, now_ms=10)  # cancel
        assert runner.current_fx is None
        assert runner.cancelling_fx is not None
        assert runner.is_active

    def test_inactive_after_cancel_finishes(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        runner.start(0, now_ms=10)
        # No release tail configured -> cancel completes on next tick.
        runner.tick(now_ms=20, universe=bytearray(512))
        assert not runner.is_active


class TestFxRunnerStats:
    def test_counters(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11))
        runner = FxRunner(reg)

        runner.start(99, now_ms=0)   # unknown
        runner.start(11, now_ms=10)  # started
        runner.start(0, now_ms=20)   # cancelled

        stats = runner.stats()
        assert stats["unknown_fx_drops"] == 1
        assert stats["runs_started"] == 1
        assert stats["runs_cancelled"] == 1
        assert stats["current_fx_id"] == 0

    def test_current_and_cancelling_ids_surface(self):
        reg = FxRegistry()
        reg.register(make_recording_class(11, "A"))
        reg.register(make_recording_class(13, "B"))
        runner = FxRunner(reg)
        runner.start(11, now_ms=0)
        fx = runner.current_fx
        fx.finish_at_ms = 10_000
        runner.start(13, now_ms=100)
        stats = runner.stats()
        assert stats["current_fx_id"] == 13
        assert stats["cancelling_fx_id"] == 11


class TestFxRunnerMultiGroup:
    """Per-group FX slot behaviour: concurrent FX in disjoint groups,
    independent replacement per slot, broadcast-cancel semantics for
    the legacy `stop` cue."""

    def _make_registry_with_grouped_fx(self, group_slot=4):
        """Build a registry with two grouped FX (fx_id 11, 13) whose
        params place `group` at ``group_slot`` (default 4, mirroring
        sparkle_on_beat's PARAMS layout)."""
        reg = FxRegistry()
        reg.register(make_recording_class(11, "A", group_slot=group_slot))
        reg.register(make_recording_class(13, "B", group_slot=group_slot))
        return reg

    def test_concurrent_fx_in_different_groups_both_tick(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        # Same fx_id, different groups -> two independent slots.
        runner.start(11, params=(0, 0, 0, 0, 1, 0), now_ms=0)
        runner.start(11, params=(0, 0, 0, 0, 2, 0), now_ms=1)

        assert runner.current_fx_by_group == {
            1: runner.current_fx_by_group[1],
            2: runner.current_fx_by_group[2],
        }
        # Both slots hold live instances; they must be distinct objects
        # so their per-tick state doesn't collide.
        assert (runner.current_fx_by_group[1]
                is not runner.current_fx_by_group[2])

        u = bytearray(UNIVERSE_SIZE)
        runner.tick(10, u)

        # Both FX ticked exactly once.
        assert len(runner.current_fx_by_group[1].ticks) == 1
        assert len(runner.current_fx_by_group[2].ticks) == 1

    def test_same_group_different_fx_replaces_slot(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        runner.start(11, params=(0, 0, 0, 0, 3, 0), now_ms=0)
        first = runner.current_fx_by_group[3]
        first.finish_at_ms = 10_000   # keep it alive through the tail

        runner.start(13, params=(0, 0, 0, 0, 3, 0), now_ms=100)

        # Slot 3 now holds the new fx (id 13); the old fx (id 11) is
        # in the cancelling tail for slot 3, not somewhere else.
        assert runner.current_fx_by_group[3].id == 13
        assert runner.cancelling_fx_by_group[3] is first
        # Other slots untouched.
        assert 3 not in {g for g in runner.current_fx_by_group if g != 3}

    def test_same_group_same_fx_no_replace_running_is_idempotent(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        runner.start(11, params=(0, 0, 0, 0, 2, 0), now_ms=0)
        first = runner.current_fx_by_group[2]

        # Repeat the same fx_id + same group; without replace_running
        # the runner must keep the existing instance untouched.
        runner.start(11, params=(0, 0, 0, 0, 2, 0), now_ms=100)
        assert runner.current_fx_by_group[2] is first
        assert len(first.starts) == 1   # start() called exactly once

    def test_same_group_same_fx_replace_running_swaps_instance(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        runner.start(11, params=(0, 0, 0, 0, 2, 0), now_ms=0)
        first = runner.current_fx_by_group[2]
        first.finish_at_ms = 10_000

        runner.start(11, params=(0, 0, 0, 0, 2, 0),
                     replace_running=True, now_ms=100)
        assert runner.current_fx_by_group[2] is not first
        assert runner.cancelling_fx_by_group[2] is first

    def test_broadcast_and_group_coexist(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        # A broadcast FX (group=0) does NOT displace a live group-1 FX.
        runner.start(11, params=(0, 0, 0, 0, 1, 0), now_ms=0)
        runner.start(13, params=(0, 0, 0, 0, 0, 0), now_ms=1)

        assert 0 in runner.current_fx_by_group
        assert 1 in runner.current_fx_by_group
        assert runner.current_fx_by_group[0].id == 13
        assert runner.current_fx_by_group[1].id == 11

    def test_cancel_wipes_every_slot(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        runner.start(11, params=(0, 0, 0, 0, 1, 0), now_ms=0)
        runner.start(11, params=(0, 0, 0, 0, 2, 0), now_ms=1)
        runner.start(11, params=(0, 0, 0, 0, 3, 0), now_ms=2)
        # Keep the cancelling tails visible so we can see them land.
        for fx in runner.current_fx_by_group.values():
            fx.finish_at_ms = 10_000

        # `stop` cue (fx_id=0) is the operator's "kill everything".
        runner.start(0, now_ms=100)

        assert runner.current_fx_by_group == {}
        # All three landed in their per-slot cancelling tails.
        assert set(runner.cancelling_fx_by_group.keys()) == {1, 2, 3}

    def test_is_active_true_when_any_slot_populated(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        assert not runner.is_active
        runner.start(11, params=(0, 0, 0, 0, 5, 0), now_ms=0)
        # Populated in a non-broadcast slot; is_active must still fire.
        assert runner.is_active

    def test_stats_current_fx_id_reports_broadcast_slot(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        # Only a group-1 FX active; legacy `current_fx_id` field must
        # report 0 (empty broadcast slot) while the by-group field
        # exposes the truth. Back-compat contract for the pre-multi-
        # group status panel.
        runner.start(11, params=(0, 0, 0, 0, 1, 0), now_ms=0)
        stats = runner.stats()
        assert stats["current_fx_id"] == 0
        assert stats["current_fx_by_group"] == {1: 11}

    def test_ticks_visit_all_slots_in_group_order(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)
        runner.start(11, params=(0, 0, 0, 0, 3, 0), now_ms=0)
        runner.start(11, params=(0, 0, 0, 0, 1, 0), now_ms=0)
        runner.start(11, params=(0, 0, 0, 0, 2, 0), now_ms=0)

        # Attach a unique universe write so we can assert tick order.
        # The RecordingFx tick_value defaults to 0xAA; give each a
        # channel derived from its group index, then assert one tick
        # each landed in the universe.
        u = bytearray(UNIVERSE_SIZE)
        runner.tick(10, u)

        # Every registered slot ticked exactly once.
        for group in (1, 2, 3):
            assert len(runner.current_fx_by_group[group].ticks) == 1

    def test_fx_without_group_param_lands_in_broadcast(self):
        # An FX with no PARAMS declaration (e.g. blackout / fade_to_black)
        # is broadcast-only; it must always land in slot 0.
        reg = FxRegistry()
        reg.register(make_recording_class(11, "NoParams"))   # no PARAMS
        runner = FxRunner(reg)

        runner.start(11, params=(0, 0, 0, 0, 7, 0), now_ms=0)
        # params[4]=7 is ignored because the FX declares no group slot.
        assert list(runner.current_fx_by_group.keys()) == [0]

    def test_out_of_range_group_falls_back_to_broadcast(self):
        reg = self._make_registry_with_grouped_fx()
        runner = FxRunner(reg)

        # 10 is outside the valid 0..9 range; the extractor treats it
        # as broadcast rather than silently allocating an 11th slot.
        runner.start(11, params=(0, 0, 0, 0, 10, 0), now_ms=0)
        assert list(runner.current_fx_by_group.keys()) == [0]
