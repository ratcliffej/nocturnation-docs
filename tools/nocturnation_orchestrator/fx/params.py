"""Param-unit conversion shared by the cue loader and the doc generator.

FX classes declare their parameter shape via a ``PARAMS`` class
attribute: a list of six ``(name, unit, description)`` tuples, one
per ``params[0..5]`` slot. Reserved slots use ``(None, None, "...")``.

The cue loader takes a human-friendly value (e.g. ``probability=100``
for "100%"), validates it against the declared unit, and converts to
the u8 the FX's ``start()`` consumes via ``params[i]``. The FX
implementations themselves stay positional / u8 - the unit metadata is
purely a loader / doc concern.

Units:
    u8       0..255 wire byte, written as-is.
    percent  0..100 (%); converted to 0..255 via round(v * 255 / 100).
    count    integer in an FX-specific range; not range-checked here
             (FX's start() is responsible). Passed through.
    100ms    0..255, on-wire 100 ms units (1..255 = 100 ms..25.5 s).
"""

VALID_UNITS = ("u8", "percent", "count", "100ms")


def convert_to_u8(value, unit):
    """Convert a human-friendly cue-file value to the u8 byte the FX
    consumes via ``params[i]``.

    Raises ValueError on out-of-range or unknown unit.
    """
    if unit == "u8":
        if not 0 <= value <= 255:
            raise ValueError(
                "u8 value out of range (0..255): %d" % value
            )
        return value
    if unit == "percent":
        if not 0 <= value <= 100:
            raise ValueError(
                "percent value out of range (0..100): %d" % value
            )
        return round(value * 255 / 100)
    if unit == "count":
        if not 0 <= value <= 255:
            raise ValueError(
                "count value out of range (0..255): %d" % value
            )
        return value
    if unit == "100ms":
        if not 0 <= value <= 255:
            raise ValueError(
                "100ms value out of range (0..255): %d" % value
            )
        return value
    raise ValueError("unknown unit: %r" % (unit,))


def validate_params_attr(cls):
    """Sanity-check an FX class's ``PARAMS`` attribute. Used by the
    doc generator and tests.

    Returns nothing; raises ValueError on a malformed declaration.

    No cap on slot count: a layered FX (e.g. wash + sparkle) needs
    more than the original 6 inherited from the abandoned Model A
    wire frame. Each FX declares whatever shape it wants.
    """
    params = getattr(cls, "PARAMS", None)
    if params is None:
        raise ValueError("%s missing PARAMS class attribute" % cls.__name__)
    if len(params) < 1:
        raise ValueError(
            "%s.PARAMS must have at least one entry; got 0" % cls.__name__
        )
    for i, entry in enumerate(params):
        if not isinstance(entry, tuple) or len(entry) != 3:
            raise ValueError(
                "%s.PARAMS[%d] must be a (name, unit, description) tuple"
                % (cls.__name__, i)
            )
        name, unit, _desc = entry
        if name is None:
            # Reserved slot. unit must also be None.
            if unit is not None:
                raise ValueError(
                    "%s.PARAMS[%d]: reserved slot must have unit=None"
                    % (cls.__name__, i)
                )
            continue
        if unit not in VALID_UNITS:
            raise ValueError(
                "%s.PARAMS[%d] unit %r not in %r"
                % (cls.__name__, i, unit, VALID_UNITS)
            )
