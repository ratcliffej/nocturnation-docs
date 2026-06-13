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


def make_recording_class(fx_id, name="Recording"):
    """Build a fresh Fx subclass with the given id. Returns the class."""
    return type(name, (RecordingFx,), {"id": fx_id, "name": name})


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
