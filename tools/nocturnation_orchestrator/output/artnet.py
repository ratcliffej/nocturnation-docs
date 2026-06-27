"""Art-Net DMX dispatcher.

Builds standard ArtDmx packets (opcode 0x5000) and UDP-sends them to a
chosen host:port. Targeted at the local `artnet-to-enttec-pro.py` shim
(127.0.0.1:6454) but works against any Art-Net node.

Packet layout (header + payload):
    "Art-Net\\0"     8 B
    opcode 0x5000   2 B  little-endian
    protocol 14     2 B  big-endian
    sequence        1 B  (0 = sequence disabled)
    physical        1 B  port hint, informational
    sub_uni         1 B  universe low byte
    net             1 B  universe high byte
    length          2 B  big-endian, length of DMX data (512)
    DMX data        512 B
"""

import socket
import struct

from .base import OutputDispatcher, OutputError


_ARTNET_ID = b"Art-Net\0"
_OPCODE_DMX = 0x5000
# Epic 13: NocturNation-specific Art-Net opcode. Carries a fully-formed
# NocturNation ESP-NOW frame (header + payload) over UDP so the shim
# can repackage it as Enttec label 0x10 and forward to the Stick. Chosen
# in the upper opcode range to avoid colliding with future Art-Net
# standard opcodes (DMX = 0x5000, RDM = 0x83xx, Config = 0x60xx, etc).
# Shim-side constant: artnet-to-enttec-pro.py ARTNET_OP_VENDOR_ESPNOW.
_OPCODE_VENDOR_ESPNOW = 0xFB00
_PROTOCOL_VERSION = 14
_UNIVERSE_SIZE = 512

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 6454
# QLC+ presents universes as 1-indexed in its UI ("Universe 0" doesn't
# exist there). The shim's filter (artnet-to-enttec-pro.py) defaults
# to 1 to match. Standardise the orchestrator on the same so the
# default-on-default path "just works" without anyone passing flags.
_DEFAULT_UNIVERSE = 1


def build_artdmx_packet(universe_bytes, sub_uni=1, net=0, sequence=0, physical=0):
    """Build the on-wire bytes for one ArtDmx packet.

    Returns a 530-byte bytes object (18 B header + 512 B DMX data).
    """
    if len(universe_bytes) != _UNIVERSE_SIZE:
        raise ValueError(
            "universe must be %d bytes, got %d"
            % (_UNIVERSE_SIZE, len(universe_bytes))
        )
    header = struct.pack(
        ">8sHHBBBBH",
        _ARTNET_ID,
        # opcode is little-endian in Art-Net (the only LE field in the
        # header); pack via byte-swap here so struct's `>` still works
        # on the rest.
        ((_OPCODE_DMX & 0xFF) << 8) | ((_OPCODE_DMX >> 8) & 0xFF),
        _PROTOCOL_VERSION,
        sequence,
        physical,
        sub_uni,
        net,
        _UNIVERSE_SIZE,
    )
    return header + bytes(universe_bytes)


def build_artvendor_espnow_packet(espnow_frame):
    """Build the on-wire bytes for one OpVendorEspNow packet.

    Carries a fully-formed NocturNation ESP-NOW frame (header + payload,
    up to 250 bytes) so the shim can repackage it as Enttec label 0x10
    and forward to the Stick. Layout mirrors ArtDmx's header conventions
    (Art-Net magic + LE opcode + BE protocol_version) but replaces the
    DMX-specific fields with a single big-endian length + the inner
    frame bytes.

    Layout:
      0..7    "Art-Net\\0"
      8..9    opcode 0xFB00 (little-endian on wire = 0x00 0xFB)
      10..11  protocol version 14 (big-endian)
      12..13  inner frame length (big-endian)
      14..    inner frame bytes
    """
    n = len(espnow_frame)
    if n == 0 or n > 250:
        raise ValueError(f"espnow_frame must be 1..250 bytes, got {n}")
    header = struct.pack(
        ">8sHHH",
        _ARTNET_ID,
        ((_OPCODE_VENDOR_ESPNOW & 0xFF) << 8) | ((_OPCODE_VENDOR_ESPNOW >> 8) & 0xFF),
        _PROTOCOL_VERSION,
        n,
    )
    return header + bytes(espnow_frame)


class ArtnetDispatcher(OutputDispatcher):
    name = "artnet"

    def __init__(self, host, port, sock, universe=_DEFAULT_UNIVERSE):
        self.host = host
        self.port = port
        self.universe = universe
        self._sock = sock

    @classmethod
    def open(cls, host=_DEFAULT_HOST, port=_DEFAULT_PORT,
             universe=_DEFAULT_UNIVERSE):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except Exception as exc:
            raise OutputError("could not open UDP socket: %s" % exc) from exc
        return cls(host=host, port=port, sock=sock, universe=universe)

    def send(self, universe):
        sub_uni = self.universe & 0xFF
        net = (self.universe >> 8) & 0xFF
        packet = build_artdmx_packet(universe, sub_uni=sub_uni, net=net)
        self._sock.sendto(packet, (self.host, self.port))

    def send_espnow_frame(self, frame: bytes) -> bool:
        """Forward a NocturNation ESP-NOW frame via the OpVendorEspNow
        Art-Net opcode. The artnet-to-enttec-pro shim recognises the
        opcode and repackages as Enttec label 0x10 for the Stick.

        Returns True on a successful sendto (UDP is fire-and-forget;
        the radio side is best-effort by nature). False on encode error.
        """
        try:
            packet = build_artvendor_espnow_packet(frame)
            self._sock.sendto(packet, (self.host, self.port))
            return True
        except Exception:
            return False

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
