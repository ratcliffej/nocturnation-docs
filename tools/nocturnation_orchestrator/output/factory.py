"""Dispatcher factory + auto-mode policy."""

from .artnet import ArtnetDispatcher
from .base import OutputError
from .usb import UsbDispatcher


def create_dispatcher(mode="auto", *, usb_port=None, usb_baud=None,
                      artnet_host="127.0.0.1", artnet_port=6454,
                      log=print):
    """Build an OutputDispatcher for the requested mode.

    Args:
        mode (str): one of 'auto', 'usb', 'artnet'.
        usb_port (str | None): explicit serial path; auto-pick if None.
        usb_baud (int | None): override baud; nocturnation_dmx default
            applies when None.
        artnet_host / artnet_port: target for Art-Net producer mode.
        log (callable): receives one-line status strings.

    'auto' tries USB first. If a StickC-shaped port is available and
    opens, return USB. Otherwise fall back to Art-Net (which always
    succeeds opening a local UDP socket - whether anything is
    listening on the other end is the user's problem).
    """
    mode = (mode or "auto").lower()
    if mode == "usb":
        d = UsbDispatcher.open(port=usb_port, baud=usb_baud)
        log("output: USB on %s" % d.port)
        return d
    if mode == "artnet":
        d = ArtnetDispatcher.open(host=artnet_host, port=artnet_port)
        log("output: Art-Net to %s:%d" % (d.host, d.port))
        return d
    if mode == "auto":
        try:
            d = UsbDispatcher.open(port=usb_port, baud=usb_baud)
            log("output: USB on %s (auto)" % d.port)
            return d
        except OutputError as exc:
            log("output: USB unavailable (%s); falling back to Art-Net" % exc)
            d = ArtnetDispatcher.open(host=artnet_host, port=artnet_port)
            log("output: Art-Net to %s:%d (auto)" % (d.host, d.port))
            return d
    raise OutputError("unknown output mode %r" % mode)
