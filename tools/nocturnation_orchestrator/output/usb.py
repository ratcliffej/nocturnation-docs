"""USB-direct dispatcher using the existing nocturnation_dmx UsbWriter."""

from nocturnation_dmx.port_picker import find_candidate_ports_with_info
from nocturnation_dmx.usb_writer import UsbWriter

from .base import OutputDispatcher, OutputError


def _pick_first_stickc_port():
    """First StickC-shaped USB port. find_candidate_ports_with_info()
    already excludes Tildagons, so any hit is a valid target."""
    candidates = find_candidate_ports_with_info()
    if not candidates:
        raise OutputError(
            "no StickC-shaped USB serial port found; plug a Plus2 / S3 in or "
            "use --output artnet"
        )
    device, _desc = candidates[0]
    return device


class UsbDispatcher(OutputDispatcher):
    name = "usb"

    def __init__(self, port, writer):
        self.port = port
        self._writer = writer

    @classmethod
    def open(cls, port=None, baud=None):
        """Open a serial port. ``port`` defaults to the first StickC
        match. Raises OutputError if no candidate or the open failed
        (auto mode catches and falls through to Art-Net)."""
        if port is None:
            port = _pick_first_stickc_port()
        writer = UsbWriter(port) if baud is None else UsbWriter(port, baud=baud)
        if not writer.is_open:
            raise OutputError(
                "could not open %s (already in use, or wrong port)" % port
            )
        return cls(port=port, writer=writer)

    def send(self, universe):
        self._writer.write_universe(universe)

    def close(self):
        try:
            self._writer.close()
        except Exception:
            pass
