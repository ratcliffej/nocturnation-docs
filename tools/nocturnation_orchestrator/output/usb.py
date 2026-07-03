"""USB-direct dispatcher using the existing nocturnation_dmx UsbWriter."""

import sys
import time

from nocturnation_dmx.port_picker import (
    find_candidate_ports_with_info,
    interactive_pick_port,
)
from nocturnation_dmx.usb_writer import UsbWriter

from .base import OutputDispatcher, OutputError


# Seconds to wait between reopen attempts after a write error closes
# the writer. UsbWriter.write_* closes itself on any SerialException /
# OSError (including a >100 ms write_timeout, or an S3 native-USB
# re-enumeration), so without a bounded reopen a single transient
# stall would wedge the dispatcher closed for the rest of the show -
# every subsequent tick would raise "UsbWriter is not open" and the
# fleet would freeze on the last frame. ~1 s recovers quickly without
# hammering open() on a genuinely unplugged Stick.
REOPEN_INTERVAL_S = 1.0


def _pick_stickc_port():
    """Pick a StickC-shaped USB serial port.

    Zero candidates -> OutputError (auto-mode catches this and falls
    through to Art-Net). One candidate -> auto-pick silently. Two or
    more -> prompt the operator with a numbered menu (same UX as the
    Art-Net shim). Operator-cancel raises OutputError so auto-mode
    can fall through cleanly.

    find_candidate_ports_with_info() already excludes Tildagons, so
    any hit is a valid Director-Stick target.
    """
    candidates = find_candidate_ports_with_info()
    if not candidates:
        raise OutputError(
            "no StickC-shaped USB serial port found; plug a Plus2 / S3 in or "
            "use --output artnet"
        )
    if len(candidates) == 1:
        device, _desc = candidates[0]
        return device
    # Multiple candidates: defer to the shim's interactive picker so
    # the orchestrator and shim share the same numbered-prompt UX.
    # Reuses find_candidate_ports_with_info internally - the double
    # lookup is cheap (pyserial just walks /dev).
    if not sys.stdin.isatty():
        # Non-interactive context (e.g. launched from a wrapper / CI):
        # don't block waiting for input; fall through to the first
        # candidate and let the operator override with --usb-port if
        # the auto-pick is wrong.
        device, _desc = candidates[0]
        return device
    chosen = interactive_pick_port()
    if chosen is None:
        raise OutputError(
            "no StickC port selected; operator cancelled the picker. "
            "Pass --usb-port to skip the prompt or --output artnet."
        )
    return chosen


class UsbDispatcher(OutputDispatcher):
    name = "usb"

    def __init__(self, port, writer):
        self.port = port
        self._writer = writer
        # Monotonic deadline before which we won't retry a reopen.
        self._reopen_after = 0.0

    def _ensure_open(self):
        """Best-effort, rate-limited reopen of a writer that a prior
        write error closed. Returns True if the writer is open.

        UsbWriter.write_* closes itself on any SerialException / OSError
        (including a >100 ms write_timeout), so without this a single
        transient stall would wedge the dispatcher closed for the rest
        of the show. Rebuild the writer at most once per
        REOPEN_INTERVAL_S so a genuinely unplugged / re-enumerating
        Stick isn't hammered on every tick.
        """
        if self._writer.is_open:
            return True
        now = time.monotonic()
        if now < self._reopen_after:
            return False
        self._reopen_after = now + REOPEN_INTERVAL_S
        self._writer = UsbWriter(self.port, baud=self._writer.baud)
        return self._writer.is_open

    @classmethod
    def open(cls, port=None, baud=None):
        """Open a serial port. ``port`` defaults to the first StickC
        match. Raises OutputError if no candidate or the open failed
        (auto mode catches and falls through to Art-Net)."""
        if port is None:
            port = _pick_stickc_port()
        writer = UsbWriter(port) if baud is None else UsbWriter(port, baud=baud)
        if not writer.is_open:
            raise OutputError(
                "could not open %s (already in use, or wrong port)" % port
            )
        return cls(port=port, writer=writer)

    def send(self, universe):
        if not self._ensure_open():
            raise OutputError("USB writer closed; reopen pending")
        self._writer.write_universe(universe)

    def send_espnow_frame(self, frame: bytes) -> bool:
        """Forward a NocturNation ESP-NOW frame through the Stick via
        the Enttec passthrough envelope (label 0x10). Director-side
        DMX bridge mode unwraps it and broadcasts onto the radio.

        Returns False if the underlying writer is closed / errored on
        write; the caller logs and continues. True on a successful
        write attempt (the radio side is best-effort by nature).
        """
        if not self._ensure_open():
            return False
        try:
            self._writer.write_espnow_frame(frame)
            return True
        except Exception:
            return False

    def close(self):
        try:
            self._writer.close()
        except Exception:
            pass
