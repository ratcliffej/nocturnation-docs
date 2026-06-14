"""FX runner.

Drives the single-slot FX execution model. The runner owns one
``current_fx`` at a time; a fresh ``start()`` cancels the existing one
(which may still be in its fade-out window) and starts the new FX in
parallel during the overlap.

Layered FX (multiple FX writing the universe concurrently as a
deliberate composition, not just a release-tail overlap) is out of
scope for Epic 10 and reserved for a later epic.

The runner is universe-only: it doesn't know about USB ports, Art-Net
sockets, or the OS now-playing backend. Concrete FX subclasses write
DMX channel values into the ``universe`` bytearray during ``tick()``;
the orchestrator main loop hands the universe to the output
dispatcher after each runner tick.

Usage::

    runner = FxRunner(fx_registry, default_bpm=120)
    universe = bytearray(512)
    # YAML cue scheduler hits a cue at t=12.0s:
    runner.start(fx_id=11, bpm=130, buildup_s=2,
                 params=(80, 200, 200, 200, 0, 0),
                 position_ms=12_000, now_ms=time_ms())
    # ~50 Hz render loop:
    runner.tick(time_ms(), universe)
    dispatcher.send(universe)
"""


class FxRunner:
    """Runs at most one FX (plus its release-fade tail) at a time.

    Attributes:
        registry (FxRegistry): where fx_id -> class lookups go.
        default_bpm (int): used when a ``start()`` call passes ``bpm=0``.

    State (read-only outside the class):
        current_fx: the active FX instance, or None.
        cancelling_fx: an FX that was superseded but is still ticking
            through its release tail. Goes to None once is_finished()
            returns True.
    """

    __slots__ = (
        "registry",
        "default_bpm",
        "current_fx",
        "cancelling_fx",
        "_unknown_fx_drops",
        "_runs_started",
        "_runs_cancelled",
    )

    def __init__(self, registry, default_bpm=120):
        self.registry = registry
        self.default_bpm = default_bpm
        self.current_fx = None
        self.cancelling_fx = None
        self._unknown_fx_drops = 0
        self._runs_started = 0
        self._runs_cancelled = 0

    @property
    def is_active(self):
        """True when an FX is currently writing the universe (either
        the foreground FX or a cancelling release tail). The
        orchestrator main loop gates DMX dispatch on this so a
        long-running orchestrator looks like an idle DMX console when
        nothing is loaded, matching QLC+'s ACTIVE / IDLE behaviour."""
        return self.current_fx is not None or self.cancelling_fx is not None

    # ------------------------------------------------------------------
    # Admission

    def start(self, fx_id, *, bpm=0, buildup_s=0,
              params=(0, 0, 0, 0, 0, 0), position_ms=0,
              replace_running=False, now_ms):
        """Admit an FX. Called by the YAML cue scheduler.

        Behaviour:
          - ``fx_id == 0``: cancel any running FX. The cancelling FX
            may continue to tick through its release tail. Equivalent
            to calling ``cancel(now_ms)``.
          - ``fx_id`` known and != current_fx.id, or ``replace_running``
            set: cancel current, start new.
          - ``fx_id`` known and == current_fx.id, no replace: ignore
            (let the existing run continue undisturbed). This makes
            repeated cue emission (e.g. the scheduler re-firing at the
            start of every bar) idempotent.
          - ``fx_id`` unknown: drop silently, bump diagnostic counter.

        Args:
            fx_id (int): 1..254 to start an FX, 0 to cancel.
            bpm (int): 0 = use ``default_bpm``, else override.
            buildup_s (int): seconds of buildup ramp.
            params (tuple[int, int, int, int, int, int]): six u8s
                whose meaning is FX-specific.
            position_ms (int): offset into the FX timeline for
                late-join.
            replace_running (bool): force-restart even if the same
                ``fx_id`` is already current.
            now_ms (int): wall-clock reference (keyword-only).
        """
        if fx_id == 0:
            self._begin_cancel(now_ms)
            return

        cls = self.registry.get(fx_id)
        if cls is None:
            self._unknown_fx_drops += 1
            return

        if (self.current_fx is not None
                and self.current_fx.id == fx_id
                and not replace_running):
            return

        self._begin_cancel(now_ms)
        effective_bpm = bpm if bpm != 0 else self.default_bpm
        new_fx = cls()
        new_fx.start(
            bpm=effective_bpm,
            buildup_s=buildup_s,
            params=params,
            position_ms=position_ms,
            now_ms=now_ms,
        )
        self.current_fx = new_fx
        self._runs_started += 1

    def cancel(self, now_ms):
        """Cancel the current FX. Convenience wrapper for ``start(0)``."""
        self._begin_cancel(now_ms)

    # ------------------------------------------------------------------
    # Tick

    def tick(self, now_ms, universe):
        """Advance both the current FX and any cancelling FX.

        Called from the orchestrator render loop at ~50 Hz. The
        cancelling FX ticks first so the current FX has the final say
        on any channels they both touch.
        """
        if self.cancelling_fx is not None:
            self.cancelling_fx.tick(now_ms, universe)
            if self.cancelling_fx.is_finished(now_ms):
                self.cancelling_fx = None
        if self.current_fx is not None:
            self.current_fx.tick(now_ms, universe)
            if self.current_fx.is_finished(now_ms):
                self.current_fx = None

    # ------------------------------------------------------------------
    # Diagnostics

    def stats(self):
        """Return a dict of counter values. Used by the orchestrator
        status panel and tests."""
        return {
            "current_fx_id":
                self.current_fx.id if self.current_fx is not None else 0,
            "cancelling_fx_id":
                self.cancelling_fx.id if self.cancelling_fx is not None else 0,
            "runs_started": self._runs_started,
            "runs_cancelled": self._runs_cancelled,
            "unknown_fx_drops": self._unknown_fx_drops,
        }

    # ------------------------------------------------------------------
    # Internal

    def _begin_cancel(self, now_ms):
        """Move ``current_fx`` -> ``cancelling_fx`` and call its
        ``cancel()``.

        If ``cancelling_fx`` was already non-None (rapid succession of
        FX changes), the older cancelling FX is dropped - the runner
        tracks one release tail at a time. Fade-outs are short enough
        that this is rarely a concern in practice.
        """
        if self.current_fx is None:
            return
        self.current_fx.cancel(now_ms)
        self.cancelling_fx = self.current_fx
        self.current_fx = None
        self._runs_cancelled += 1
