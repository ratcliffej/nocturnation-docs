# SPDX-License-Identifier: BSD-3-Clause
"""nocturnation_dmx - shared building blocks for NocturNation laptop-side tools.

Both `artnet-to-enttec-pro.py` (the existing QLC+ shim) and
`nowplaying-orchestrator.py` (Epic 10's music-driven orchestrator)
compose against this library, so the Enttec Pro framing, StickC port
detection, and USB write loop live in exactly one place.

Submodules:

    enttec_pro    Frame builder: payload bytes -> Enttec DMX USB Pro frame.
    port_picker   Detect candidate StickC serial ports; exclude Tildagons.
    usb_writer    Open the serial port and stream Enttec frames.

The submodules are intentionally small and stateless except where state
is genuinely required (the writer holds a serial handle). Callers compose
them; the library does not host a main loop.
"""

from .enttec_pro import (
    ENTTEC_BAUD,
    ENTTEC_DMX_START_CODE,
    ENTTEC_END,
    ENTTEC_LABEL_ESPNOW,
    ENTTEC_LABEL_OUTPUT,
    ENTTEC_START,
    DMX_UNIVERSE_BYTES,
    wrap_enttec_espnow,
    wrap_enttec_pro,
)
from . import espnow_frame
from .port_picker import (
    describe_port,
    device_kind_from_port,
    find_candidate_ports,
    find_candidate_ports_with_info,
    interactive_pick_port,
    port_looks_like_tildagon,
)
from .usb_writer import UsbWriter, open_serial

__all__ = [
    # enttec_pro
    "ENTTEC_BAUD",
    "ENTTEC_DMX_START_CODE",
    "ENTTEC_END",
    "ENTTEC_LABEL_ESPNOW",
    "ENTTEC_LABEL_OUTPUT",
    "ENTTEC_START",
    "DMX_UNIVERSE_BYTES",
    "wrap_enttec_espnow",
    "wrap_enttec_pro",
    # espnow_frame (submodule, Epic 13)
    "espnow_frame",
    # port_picker
    "describe_port",
    "device_kind_from_port",
    "find_candidate_ports",
    "find_candidate_ports_with_info",
    "interactive_pick_port",
    "port_looks_like_tildagon",
    # usb_writer
    "UsbWriter",
    "open_serial",
]
