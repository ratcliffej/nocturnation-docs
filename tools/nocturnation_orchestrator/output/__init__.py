"""DMX universe output dispatchers.

A dispatcher takes a 512-byte bytearray (the universe state after one
FX engine tick) and emits it as either:

- Enttec Pro frames over USB-CDC (via nocturnation_dmx.UsbWriter)
  - standalone path, no other software needed
- Art-Net DMX packets to a UDP socket
  - sends to a co-running shim (or any Art-Net node) at the chosen
    host:port

Auto mode tries USB first; if the port is in use (typical when the
Art-Net shim is already running), falls back to Art-Net producer mode
targeting 127.0.0.1:6454. This lets the orchestrator coexist with the
shim or replace it cleanly depending on what's open.
"""

from .base import OutputDispatcher, OutputError
from .factory import create_dispatcher

__all__ = [
    "OutputDispatcher",
    "OutputError",
    "create_dispatcher",
]
