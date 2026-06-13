"""FX base class.

Each concrete FX subclasses `Fx`, sets the class-level metadata (id,
name, category), and implements `start` / `tick` / `cancel` /
`is_finished`. The `FxRunner` instantiates one FX at a time and calls
these methods.

The contract is enforced by NotImplementedError rather than `abc`;
matches the abandoned Tildagon-side framework that was the starting
point, and keeps the surface small for FX authors.

Universe model: every `tick(now_ms, universe)` receives a 512-byte
`bytearray` representing one DMX universe. Channels are 1-indexed in
the LD-facing world, so write `universe[channel - 1] = value` (use the
`set_ch` helper). The dispatch layer takes the universe verbatim after
each tick - no merging, no per-FX masking. If two FX touch the same
channel (current + cancelling release tail), the FX authored later in
the tick sequence wins.
"""

UNIVERSE_SIZE = 512


def set_ch(universe, channel, value):
    """Write a single DMX channel value into the universe.

    ``channel`` is 1..512 (DMX-numbering, as the LD sees it).
    ``value`` is clamped to 0..255.
    """
    if channel < 1 or channel > UNIVERSE_SIZE:
        raise ValueError("DMX channel out of range: %d" % channel)
    if value < 0:
        value = 0
    elif value > 255:
        value = 255
    universe[channel - 1] = value


class Fx:
    """Base class for FX implementations.

    Subclass attributes:
        id (int): 1..254 unique identifier, matching the YAML cue's
            `fx` field. Reserve 0 (cancel sentinel) and 255 (future).
        name (str): human-readable label for diagnostics and the
            generated FX library doc.
        category (str): one of 'ambient', 'beat', 'buildup', 'drop',
            'transition'. Convention only - the runner doesn't gate on
            category.
        default_duration_ms (int | None): if non-None and no cancel
            arrives by this time after start, `is_finished()` returns
            True automatically. None = runs indefinitely until
            cancelled.

    Lifecycle (called by FxRunner):
        start(...)            - once at admission
        tick(now_ms, universe) - every render frame (~50 Hz)
        cancel(now_ms)        - once when superseded or explicitly
                                cancelled; tick() continues firing
                                until is_finished() returns True
                                (to support fade-out release windows).
        is_finished(now_ms)   - the runner stops ticking once True.
    """

    id = 0
    name = ""
    category = ""

    default_duration_ms = None

    def __init__(self):
        self._started_ms = None
        self._cancelled_ms = None

    def start(self, *, bpm, buildup_s, params, position_ms, now_ms):
        """Initialise FX state. Called once after admission.

        Args:
            bpm (int): 0 = use orchestrator default, else override.
            buildup_s (int): seconds of buildup ramp before reaching
                full intensity / probability.
            params (tuple[int, int, int, int, int, int]): six u8s
                whose meaning is FX-specific; see Docs/fx-library.md.
            position_ms (int): offset into the FX timeline. Non-zero
                for late-join (the song was already playing when the
                orchestrator started): the FX should start as if it
                had already run for `position_ms` milliseconds.
            now_ms (int): the wall-clock reference. Subsequent tick()
                calls pass the same clock source.
        """
        self._started_ms = now_ms
        self._cancelled_ms = None
        raise NotImplementedError("Fx.start must be overridden")

    def tick(self, now_ms, universe):
        """Advance one frame. Write DMX values into ``universe``.

        Continues to be called between cancel() and is_finished() so
        the FX can render its release fade.
        """
        raise NotImplementedError("Fx.tick must be overridden")

    def cancel(self, now_ms):
        """Begin release / fade-out. The runner keeps ticking the FX
        until is_finished() returns True; this lets the FX shape its
        own exit (a fade-out, a snap-off, whatever)."""
        self._cancelled_ms = now_ms

    def is_finished(self, now_ms):
        """True when the FX should be torn down. Default behaviour:
        finished as soon as it's been cancelled, or after
        ``default_duration_ms`` if set."""
        if self._cancelled_ms is not None:
            return True
        if (self.default_duration_ms is not None
                and self._started_ms is not None
                and (now_ms - self._started_ms) >= self.default_duration_ms):
            return True
        return False
