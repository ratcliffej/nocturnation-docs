# SPDX-License-Identifier: BSD-3-Clause
"""Enttec DMX USB Pro framing.

Single source of truth for the constants and the payload-to-frame
wrapper used by the QLC+ shim and the orchestrator. Kept dependency-free
so it imports cleanly on every supported platform.
"""

# Enttec DMX USB Pro framing constants. Receivers must match these.
ENTTEC_START = 0x7E
ENTTEC_END = 0xE7
ENTTEC_LABEL_OUTPUT = 0x06
ENTTEC_DMX_START_CODE = 0x00

# Wire baud the StickC firmware listens at. 460 800 (not the conventional
# 921 600) is a Plus2 accommodation; the ESP32-PICO-V3-02 is single-core
# and shares its UART RX interrupt with ESP-NOW TX (heartbeat broadcast)
# and the LCD. At 921 600 the UART hardware FIFO holds only ~1.4 ms of
# inbound traffic - shorter than the IRQ-off windows ESP-NOW briefly
# opens during radio handoff; bytes drop silently mid-frame and the
# parser sits in WaitStart with r:0 o:0 (bench-confirmed). 460 800
# doubles the FIFO lifetime to ~2.8 ms while still giving 50 % bandwidth
# headroom over 44 fps DMX traffic. The S3's native USB-CDC ignores the
# baud value entirely, so the change is invisible there.
#
# The firmware-side default in
# nocturnation-stickc/src/dal/drivers/dmx_usb_cdc_adapter.h MUST match.
ENTTEC_BAUD = 460_800

DMX_UNIVERSE_BYTES = 512


def wrap_enttec_pro(payload: bytes) -> bytes:
    """Wrap a DMX payload in Enttec DMX USB Pro Output Only framing.

    Pads payload to 512 bytes if shorter; truncates if longer (Art-Net
    can send 2-512 channels per packet). The length field in the Enttec
    frame is the payload size INCLUDING the DMX start code byte (513
    total).

    Layout:
      0     0x7E                    start byte
      1     0x06                    label = Output Only Send DMX Packet
      2..3  513 (little-endian)     length = start code + 512 channel bytes
      4     0x00                    DMX start code (standard data, no RDM)
      5..516 channel values 1..512
      517   0xE7                    end byte
    """
    if len(payload) < DMX_UNIVERSE_BYTES:
        payload = payload + b"\x00" * (DMX_UNIVERSE_BYTES - len(payload))
    elif len(payload) > DMX_UNIVERSE_BYTES:
        payload = payload[:DMX_UNIVERSE_BYTES]
    length = DMX_UNIVERSE_BYTES + 1  # 1 byte start code + 512 channels
    return bytes((
        ENTTEC_START,
        ENTTEC_LABEL_OUTPUT,
        length & 0xFF,
        (length >> 8) & 0xFF,
        ENTTEC_DMX_START_CODE,
    )) + payload + bytes((ENTTEC_END,))
