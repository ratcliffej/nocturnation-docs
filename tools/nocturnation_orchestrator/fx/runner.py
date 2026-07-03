"""FX runner.

Drives a per-group FX execution model. The runner owns one active FX
per device group (0..9), where group 0 is broadcast and 1..9 are the
addressable device groups. A fresh ``start()`` for group G cancels
only the existing FX in slot G (which may still be in its fade-out
window); FX in other group slots keep ticking undisturbed.

This lets a single cue file emit concurrent per-group FX, e.g.::

    00:13   sparkle_on_beat 100   0 100 100 1
    00:13.1 sparkle_on_beat 100 100   0 100 2
    00:13.2 sparkle_on_beat   0 100 100 100 3

    -> three concurrent sparkles, one per group, disjoint DMX blocks.

The single-slot model shipped in Epic 10 kept only the last-started FX
alive; the multi-slot design preserves the release-tail overlap
semantics per slot. The DMX universe layout already allocates a
40-channel block per group (see channels.block_channel), so two FX in
different slots never write to the same channel by construction.

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
                 params=(80, 200, 200, 200, 1, 0),  # group=1 in params[4]
                 position_ms=12_000, now_ms=time_ms())
    # ~50 Hz render loop:
    runner.tick(time_ms(), universe)
    dispatcher.send(universe)
"""


class FxRunner:
    """Runs one FX per device group (plus each slot's release-fade tail).

    Attributes:
        registry (FxRegistry): where fx_id -> class lookups go.
        default_bpm (int): used when a ``start()`` call passes ``bpm=0``.

    State (read-only outside the class):
        current_fx: legacy accessor - returns the FX in the broadcast
            (group 0) slot, or None. Preserved for callers that
            predate the multi-group split. Use ``current_fx_by_group``
            for full multi-slot visibility.
        cancelling_fx: same shape, for the group-0 release tail.
        current_fx_by_group: dict[int, Fx] of every live foreground FX,
            keyed by target group (0 = broadcast, 1..9 = device group).
        cancelling_fx_by_group: dict[int, Fx] of every FX still ticking
            through its release tail after being superseded.
    """

    __slots__ = (
        "registry",
        "default_bpm",
        "_current_fx",
        "_cancelling_fx",
        "beats_ms",
        "_unknown_fx_drops",
        "_runs_started",
        "_runs_cancelled",
    )

    def __init__(self, registry, default_bpm=120):
        self.registry = registry
        self.default_bpm = default_bpm
        # Per-group slots. Absent key = slot empty. Group 0 is broadcast
        # (writes to universe block 0), 1..9 are the device groups.
        self._current_fx = {}
        self._cancelling_fx = {}
        # Epic 14.9 Block B. Set by the scheduler when a cue file is
        # loaded; empty list when the file has no `.cues.analysis.json`
        # sidecar. Attached to every FX instance before `start()` so
        # beat-aware FX can consult the actual grid; FX that don't
        # care just ignore the attribute.
        self.beats_ms = []
        self._unknown_fx_drops = 0
        self._runs_started = 0
        self._runs_cancelled = 0

    def set_beats(self, beats_ms):
        """Replace the runner's beats list. Called by the scheduler
        when a cue file is loaded / hot-reloaded / cleared. Pass
        an empty list (or None) to revert to bpm-clock fallback
        behaviour for subsequent FX starts."""
        self.beats_ms = list(beats_ms) if beats_ms else []

    # ------------------------------------------------------------------
    # Legacy single-slot accessors (broadcast group only)

    @property
    def current_fx(self):
        """Back-compat: FX in the broadcast (group 0) slot, or None.
        Callers that need multi-slot visibility should read
        ``current_fx_by_group`` instead."""
        return self._current_fx.get(0)

    @property
    def cancelling_fx(self):
        """Back-compat: group-0 release tail, or None."""
        return self._cancelling_fx.get(0)

    @property
    def current_fx_by_group(self):
        """Snapshot of every live foreground FX keyed by target group.
        Returned as a fresh dict so mutation on the caller side
        doesn't leak into runner state."""
        return dict(self._current_fx)

    @property
    def cancelling_fx_by_group(self):
        """Snapshot of every FX still in its release tail, keyed by
        target group."""
        return dict(self._cancelling_fx)

    @property
    def is_active(self):
        """True when ANY slot is writing the universe (foreground FX
        or cancelling release tail). The orchestrator main loop gates
        DMX dispatch on this so a long-running orchestrator looks
        like an idle DMX console when nothing is loaded, matching
        QLC+'s ACTIVE / IDLE behaviour."""
        return bool(self._current_fx) or bool(self._cancelling_fx)

    # ------------------------------------------------------------------
    # Admission

    def start(self, fx_id, *, bpm=0, buildup_s=0,
              params=(0, 0, 0, 0, 0, 0), position_ms=0,
              replace_running=False, now_ms):
        """Admit an FX into the slot for its target group.

        The target group is extracted from ``params`` by walking the
        FX class's ``PARAMS`` declaration for a slot named ``group``.
        FX without a group param land in slot 0 (broadcast).

        Behaviour:
          - ``fx_id == 0``: cancel every active slot (backwards
            compatible with the single-slot `stop` cue). Cancelling
            FX may continue to tick through their release tails.
          - ``fx_id`` known and slot G is empty, or ``replace_running``
            set: start the new FX in slot G. If slot G was occupied
            the previous FX is moved to the cancelling tail.
          - ``fx_id`` known, slot G already runs the same ``fx_id``,
            no ``replace_running``: ignore (let the existing run
            continue undisturbed). Idempotence for cue re-emission.
          - ``fx_id`` unknown: drop silently, bump diagnostic counter.

        Args:
            fx_id (int): 1..254 to start an FX, 0 to cancel every slot.
            bpm (int): 0 = use ``default_bpm``, else override.
            buildup_s (int): seconds of buildup ramp.
            params (tuple[int, ...]): six u8s whose meaning is
                FX-specific. The group slot (if the FX declares one)
                is read from here to route the FX to its slot.
            position_ms (int): offset into the FX timeline for
                late-join.
            replace_running (bool): force-restart even if the same
                ``fx_id`` is already active in the target slot.
            now_ms (int): wall-clock reference (keyword-only).
        """
        if fx_id == 0:
            self.cancel(now_ms=now_ms)
            return

        cls = self.registry.get(fx_id)
        if cls is None:
            self._unknown_fx_drops += 1
            return

        group = _extract_group(cls, params)

        existing = self._current_fx.get(group)
        if (existing is not None
                and existing.id == fx_id
                and not replace_running):
            return

        self._begin_cancel(group, now_ms)
        effective_bpm = bpm if bpm != 0 else self.default_bpm
        new_fx = cls()
        # Epic 14.9 Block B. Attach the runner's current beats list
        # (loaded from the analysis sidecar) BEFORE start() so beat-
        # aware FX can capture it. Existing FX that don't read this
        # attribute are unaffected. Empty list = "no sidecar"; FX
        # fall back to bpm-derived clock.
        new_fx.beats_ms = self.beats_ms
        new_fx.start(
            bpm=effective_bpm,
            buildup_s=buildup_s,
            params=params,
            position_ms=position_ms,
            now_ms=now_ms,
        )
        self._current_fx[group] = new_fx
        self._runs_started += 1

    def cancel(self, now_ms):
        """Cancel every active slot. Equivalent to ``start(0)``. Each
        cancelled FX continues to tick through its release tail via
        the per-slot cancelling entry."""
        for group in list(self._current_fx.keys()):
            self._begin_cancel(group, now_ms)

    # ------------------------------------------------------------------
    # Tick

    def tick(self, now_ms, universe, position_ms=None):
        """Advance every active slot: cancelling FX first, then
        foreground FX. Slots are visited in ascending group order for
        deterministic behaviour under test.

        Called from the orchestrator render loop at ~50 Hz. Cancelling
        FX tick first per slot so the foreground FX in that slot has
        the final say on any channel they both touch. Across groups
        there is no channel overlap by construction (each group owns
        a disjoint 40-channel block).

        position_ms (optional): the current music-player position.
        Stored on the FX instance before each tick so beat-aware FX
        (sparkle_on_beat, pulse_per_bar, wash_with_sparkle) can look
        up beats[] against the actual music clock rather than
        wall-clock-elapsed-since-cue-start. The wall-clock approach
        silently breaks on pause / seek. When omitted, FX fall back
        to the wall-clock math.
        """
        for group in sorted(self._cancelling_fx.keys()):
            fx = self._cancelling_fx[group]
            if position_ms is not None:
                fx.position_ms = position_ms
            fx.tick(now_ms, universe)
            if fx.is_finished(now_ms):
                del self._cancelling_fx[group]
        for group in sorted(self._current_fx.keys()):
            fx = self._current_fx[group]
            if position_ms is not None:
                fx.position_ms = position_ms
            fx.tick(now_ms, universe)
            if fx.is_finished(now_ms):
                del self._current_fx[group]

    # ------------------------------------------------------------------
    # Diagnostics

    def stats(self):
        """Return a dict of counter values. Used by the orchestrator
        status panel and tests.

        The singular ``current_fx_id`` / ``cancelling_fx_id`` fields
        report the broadcast (group 0) slot for back-compat with
        pre-multi-group callers; the ``*_by_group`` fields give the
        full picture."""
        slot0_current = self._current_fx.get(0)
        slot0_cancelling = self._cancelling_fx.get(0)
        return {
            "current_fx_id":
                slot0_current.id if slot0_current is not None else 0,
            "cancelling_fx_id":
                slot0_cancelling.id if slot0_cancelling is not None else 0,
            "current_fx_by_group":
                {g: fx.id for g, fx in sorted(self._current_fx.items())},
            "cancelling_fx_by_group":
                {g: fx.id for g, fx in sorted(self._cancelling_fx.items())},
            "runs_started": self._runs_started,
            "runs_cancelled": self._runs_cancelled,
            "unknown_fx_drops": self._unknown_fx_drops,
        }

    # ------------------------------------------------------------------
    # Internal

    def _begin_cancel(self, group, now_ms):
        """Move ``current_fx[group]`` -> ``cancelling_fx[group]`` and
        call its ``cancel()``. No-op when the slot is empty.

        If ``cancelling_fx[group]`` was already non-empty (rapid
        succession of FX changes in the same slot), the older
        cancelling FX is dropped - the runner tracks one release
        tail per slot. Fade-outs are short enough that this is rarely
        a concern in practice.
        """
        existing = self._current_fx.get(group)
        if existing is None:
            return
        existing.cancel(now_ms)
        self._cancelling_fx[group] = existing
        del self._current_fx[group]
        self._runs_cancelled += 1


def _extract_group(cls, params):
    """Return the group slot index this cue targets.

    Walks the FX class's ``PARAMS`` declaration for an entry whose
    first element is the literal string ``"group"`` and pulls the
    corresponding index out of ``params``. Returns 0 (broadcast) when
    the FX has no ``group`` param, the params tuple is too short, or
    the value is outside the valid group range 0..9."""
    param_specs = getattr(cls, "PARAMS", None) or ()
    for slot, spec in enumerate(param_specs):
        name = spec[0] if isinstance(spec, (tuple, list)) else spec
        if name == "group":
            if slot < len(params):
                g = params[slot]
                if 0 <= g <= 9:
                    return g
            return 0
    return 0
