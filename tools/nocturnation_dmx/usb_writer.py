# SPDX-License-Identifier: BSD-3-Clause
"""USB-CDC writer.

Opens a StickC serial port and writes Enttec Pro frames. Used in
standalone mode by both the shim (after Art-Net decode) and the
orchestrator (after cue resolution).

The writer holds the serial handle; closing it is the caller's
responsibility (typically by letting the writer fall out of scope or
calling .close() in a finally block).
"""

from typing import Optional

import serial

from .enttec_pro import ENTTEC_BAUD, wrap_enttec_pro


def open_serial(port: str, baud: int = ENTTEC_BAUD) -> Optional[serial.Serial]:
    """Open a serial port; return None on failure (silent).

    Caller is responsible for treating None as 'try again later' or
    'fall back to a different output mode'. The non-blocking timeouts
    (timeout=0 for reads, write_timeout=0.1 for writes) match the
    shim's existing behaviour - writes that block longer than 100 ms
    raise serial.SerialTimeoutException, which the caller can either
    surface as an error or recover from on the next frame.
    """
    try:
        return serial.Serial(port, baud, timeout=0, write_timeout=0.1)
    except (serial.SerialException, OSError):
        return None


class UsbWriter:
    """Owns a serial handle and writes Enttec Pro frames.

    The writer accepts a raw 512-byte DMX universe payload (or shorter
    - it'll be padded), wraps it as an Enttec Pro frame, and writes
    the frame to the underlying serial port. Errors are surfaced as
    serial.SerialException / OSError - the caller decides whether to
    log + retry or escalate.

    Typical use::

        writer = UsbWriter("/dev/cu.usbserial-XXX")
        if writer.is_open:
            writer.write_universe(payload)
        writer.close()

    Or in a context manager::

        with UsbWriter("/dev/cu.usbserial-XXX") as writer:
            writer.write_universe(payload)
    """

    def __init__(self, port: str, baud: int = ENTTEC_BAUD):
        self.port = port
        self.baud = baud
        self._serial: Optional[serial.Serial] = open_serial(port, baud)

    @property
    def is_open(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def write_universe(self, payload: bytes) -> None:
        """Wrap payload in Enttec Pro framing and write to USB.

        Raises serial.SerialException or OSError on write failure;
        the writer is left in a closed state in that case.
        """
        if self._serial is None or not self._serial.is_open:
            raise serial.SerialException("UsbWriter is not open")
        frame = wrap_enttec_pro(payload)
        try:
            self._serial.write(frame)
        except (serial.SerialException, OSError):
            # Mark closed so callers know they need to reopen
            self.close()
            raise

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
