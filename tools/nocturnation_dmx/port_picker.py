# SPDX-License-Identifier: BSD-3-Clause
"""StickC serial port detection.

Cross-platform detection of likely NocturNation StickC ports, with
explicit exclusion of Tildagons (which enumerate as USB-CDC on the same
/dev/cu.usbmodem* path range but cannot run the DMX Bridge role).

Used by both the QLC+ shim (`artnet-to-enttec-pro.py`) and the
orchestrator (`nowplaying-orchestrator.py`).
"""

import sys
from typing import Optional

import serial.tools.list_ports


# Substrings (case-insensitive) that flag a port as a Tildagon.
# The Tildagon enumerates as 'Electromagnetic Field TiLDAGON' on macOS;
# checking both halves catches drivers that surface only one.
_TILDAGON_DESC_HINTS = ("tildagon", "electromagnetic field")


def describe_port(port) -> str:
    """Best-effort human label for a serial.tools.list_ports.ListPortInfo.

    Combines the manufacturer / product strings the OS exposes for the
    USB device behind the serial port, so the picker can show
    'M5StickC Plus2' or 'Espressif S3' rather than just opaque
    /dev/cu.usbmodemNNNN paths.
    """
    bits = []
    if getattr(port, "manufacturer", None):
        bits.append(str(port.manufacturer))
    if getattr(port, "product", None):
        bits.append(str(port.product))
    if not bits and getattr(port, "description", None):
        bits.append(str(port.description))
    return " ".join(bits) if bits else "(unknown device)"


def port_looks_like_tildagon(port) -> bool:
    """True if the OS-reported description suggests this port is a Tildagon.

    Substring check is case-insensitive and covers both the manufacturer
    ('Electromagnetic Field') and the product ('TiLDAGON') strings.
    """
    desc = describe_port(port).lower()
    return any(hint in desc for hint in _TILDAGON_DESC_HINTS)


def device_kind_from_port(port_path: str) -> str:
    """Identify whether a serial port is a Tildagon or something else.

    Used to refuse explicit --port pointing at a Tildagon. Returns
    'tildagon' or 'other'.
    """
    for port in serial.tools.list_ports.comports():
        if port.device == port_path:
            return "tildagon" if port_looks_like_tildagon(port) else "other"
    return "other"


def find_candidate_ports_with_info() -> list[tuple[str, str]]:
    """List likely NocturNation StickC serial ports with a human
    description of each, used by the interactive picker.

    Tildagon serial ports are deliberately excluded - the Tildagon
    cannot run the DMX Bridge role (the badge OS owns the USB-CDC
    endpoint and the firmware has no DMX Bridge mode). Only the
    StickC (S3 native USB-CDC; Plus2 FTDI/CH340 USB-UART) is a valid
    target.

    Device-path matching by platform:
      - macOS S3 (native USB-CDC): /dev/cu.usbmodemNNNN
      - macOS Plus2 (FTDI / CP210x / CH340): /dev/cu.usbserial-XXXX,
        /dev/cu.SLAB_USBtoUART, /dev/cu.wchusbserialXXXX
      - Linux: /dev/ttyACMn (CDC) or /dev/ttyUSBn (UART bridge)
      - Windows: COMn (any; descriptions disambiguate)
    """
    candidates = []
    for port in serial.tools.list_ports.comports():
        device = port.device
        is_candidate = (
            "usbmodem"      in device
            or "usbserial"  in device
            or "SLAB_"      in device                # macOS CP210x
            or "wchusb"     in device                # macOS CH340 (some drivers)
            or "ttyACM"     in device                # Linux CDC
            or "ttyUSB"     in device                # Linux USB-UART bridge
            or device.upper().startswith("COM")      # Windows
        )
        if not is_candidate:
            continue
        if port_looks_like_tildagon(port):
            continue
        candidates.append((device, describe_port(port)))
    return candidates


def find_candidate_ports() -> list[str]:
    """Wrapper that returns just the device paths (no descriptions)."""
    return [device for device, _desc in find_candidate_ports_with_info()]


def interactive_pick_port() -> Optional[str]:
    """Show the available serial ports and let the operator pick one.

    Returns the chosen device path, or None if the user cancels or
    there are no candidates. Skips the prompt entirely when there's
    only one candidate (no choice to make).
    """
    candidates = find_candidate_ports_with_info()
    if not candidates:
        print("No StickC serial port detected. Plug in your StickC "
              "(Plus2 or S3) running DMX Bridge mode and try again, "
              "or pass --port explicitly. Tildagon devices are "
              "excluded - the DMX Bridge role is StickC-only.",
              file=sys.stderr)
        return None
    if len(candidates) == 1:
        device, desc = candidates[0]
        print(f"Auto-selecting only available port: {device}  ({desc})")
        return device
    print("Multiple serial ports detected. Pick one:")
    for idx, (device, desc) in enumerate(candidates, start=1):
        print(f"  [{idx}] {device:30}  {desc}")
    while True:
        try:
            choice = input(
                f"Enter 1-{len(candidates)} (or q to quit): "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            return None
        if choice in ("q", "quit", "exit"):
            return None
        try:
            n = int(choice)
            if 1 <= n <= len(candidates):
                return candidates[n - 1][0]
        except ValueError:
            pass
        print("  not a valid choice; try again.")
