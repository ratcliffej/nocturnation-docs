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
_PROTOCOL_VERSION = 14
_UNIVERSE_SIZE = 512

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 6454


def build_artdmx_packet(universe_bytes, sub_uni=0, net=0, sequence=0, physical=0):
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


class ArtnetDispatcher(OutputDispatcher):
    name = "artnet"

    def __init__(self, host, port, sock):
        self.host = host
        self.port = port
        self._sock = sock

    @classmethod
    def open(cls, host=_DEFAULT_HOST, port=_DEFAULT_PORT):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except Exception as exc:
            raise OutputError("could not open UDP socket: %s" % exc) from exc
        return cls(host=host, port=port, sock=sock)

    def send(self, universe):
        packet = build_artdmx_packet(universe)
        self._sock.sendto(packet, (self.host, self.port))

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass
