# SPDX-License-Identifier: BSD-3-Clause
"""NocturNation ESP-NOW frame encoder (laptop side).

Mirrors the C++ wire format defined in
nocturnation-stickc/include/transport/espnow/frame.h. Used by the
orchestrator (Epic 10 / 13) to build display-content frames
(TEXT_DISPLAY, CLEAR_SCREEN, eventually BITMAP_*) that the StickC
Director, while in DMX bridge mode, forwards verbatim onto the
ESP-NOW radio via the kLabelEspNowBroadcast Enttec passthrough.

The wash family is NOT encoded here; it's emitted by the bridge's
own DMX-channel mappers and stays inside the firmware.

Frame layout (v2, see firmware frame.h for the canonical reference):

    Offset  Field             Size  Notes
    0       magic[0]          1     0x4E ('N')
    1       magic[1]          1     0x4E ('N')
    2       protocol_version  1     0x02
    3       source_id         1     1-254; 0xFF = broadcast
    4       sequence_number   1     1-255 wrapping; 0 = sequencing off
    5       hop_count         1     0 = original
    6       message_type      1     MessageType enum
    7       payload_len       1     bytes of payload following
    8+      payload           N     type-specific (little-endian)
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

# ---------------------------------------------------------------------------
# Wire-format constants. Keep in sync with frame.h.
# ---------------------------------------------------------------------------

MAGIC_0: Final[int] = 0x4E
MAGIC_1: Final[int] = 0x4E
PROTOCOL_VERSION: Final[int] = 0x02
BROADCAST_SOURCE_ID: Final[int] = 0xFF

HEADER_SIZE: Final[int] = 8
MAX_FRAME_SIZE: Final[int] = 250
MAX_PAYLOAD_SIZE: Final[int] = MAX_FRAME_SIZE - HEADER_SIZE  # 242

# Source-id partitioning (protocol manual §3.4).
SOURCE_ID_COMMUNITY_MIN: Final[int] = 0x00
SOURCE_ID_COMMUNITY_MAX: Final[int] = 0x3F
SOURCE_ID_PERFORMANCE_MIN: Final[int] = 0x40
SOURCE_ID_PERFORMANCE_MAX: Final[int] = 0xFE


class MessageType(IntEnum):
    HEARTBEAT = 0x00
    LIGHT_PULSE = 0x03
    LIGHT_WASH = 0x06
    LIGHT_WASH_END = 0x07
    LIGHT_WASH_PULSE = 0x08
    # Epic 13: display-content family.
    TEXT_DISPLAY = 0x09
    BITMAP_HEADER = 0x0A
    BITMAP_PLANE = 0x0B
    CLEAR_SCREEN = 0x0C
    EXTENSION = 0xFF


# ---------------------------------------------------------------------------
# Per-payload size limits + offsets.
# ---------------------------------------------------------------------------

TEXT_DISPLAY_MAX_HEADER_LEN: Final[int] = 64
TEXT_DISPLAY_MAX_BODY_LEN: Final[int] = 128
TEXT_DISPLAY_FIXED_PREFIX_LEN: Final[int] = 6   # group + r + g + b + ttl_ms_le

CLEAR_SCREEN_PAYLOAD_LEN: Final[int] = 3


# ---------------------------------------------------------------------------
# Header builder
# ---------------------------------------------------------------------------


def _build_header(
    source_id: int,
    sequence: int,
    hop_count: int,
    message_type: MessageType,
    payload_len: int,
) -> bytes:
    """Pack the 8-byte NocturNation v2 header.

    Caller is responsible for source_id / sequence / hop_count semantics
    (the Director chooses these when emitting; the orchestrator running
    on the laptop typically sets source_id = BROADCAST_SOURCE_ID (0xFF)
    and an incrementing sequence so Lumes' dedup ring sees each frame as
    unique).
    """
    if not (0 <= source_id <= 0xFF):
        raise ValueError(f"source_id out of range: {source_id}")
    if not (0 <= sequence <= 0xFF):
        raise ValueError(f"sequence out of range: {sequence}")
    if not (0 <= hop_count <= 0xFF):
        raise ValueError(f"hop_count out of range: {hop_count}")
    if not (0 <= payload_len <= 0xFF):
        raise ValueError(f"payload_len out of range: {payload_len}")
    return bytes(
        (
            MAGIC_0,
            MAGIC_1,
            PROTOCOL_VERSION,
            source_id,
            sequence,
            hop_count,
            int(message_type),
            payload_len,
        )
    )


# ---------------------------------------------------------------------------
# TEXT_DISPLAY (variable-length, 8..200 bytes payload)
# ---------------------------------------------------------------------------


def encode_text_display(
    *,
    source_id: int = BROADCAST_SOURCE_ID,
    sequence: int = 1,
    hop_count: int = 0,
    target_group: int = 0,
    r: int = 255,
    g: int = 255,
    b: int = 255,
    ttl_ms: int = 0,
    header: str = "",
    body: str = "",
) -> bytes:
    """Encode a TEXT_DISPLAY frame.

    header / body are UTF-8 strings; they're encoded to bytes and length-
    prefixed inline per the wire format. header is capped at 64 bytes
    (UTF-8 encoded), body at 128 bytes. ttl_ms = 0 means sticky (held
    until CLEAR_SCREEN); non-zero is auto-clear after the elapsed window.

    Raises ValueError if any string exceeds its byte cap (caller should
    truncate or chunk client-side - the firmware silently rejects
    over-cap frames).
    """
    if not (0 <= target_group <= 0xFF):
        raise ValueError(f"target_group out of range: {target_group}")
    for name, v in (("r", r), ("g", g), ("b", b)):
        if not (0 <= v <= 0xFF):
            raise ValueError(f"{name} out of range: {v}")
    if not (0 <= ttl_ms <= 0xFFFF):
        raise ValueError(f"ttl_ms out of range: {ttl_ms}")

    header_bytes = header.encode("utf-8")
    body_bytes = body.encode("utf-8")
    if len(header_bytes) > TEXT_DISPLAY_MAX_HEADER_LEN:
        raise ValueError(
            f"header is {len(header_bytes)} bytes (UTF-8); "
            f"max {TEXT_DISPLAY_MAX_HEADER_LEN}"
        )
    if len(body_bytes) > TEXT_DISPLAY_MAX_BODY_LEN:
        raise ValueError(
            f"body is {len(body_bytes)} bytes (UTF-8); "
            f"max {TEXT_DISPLAY_MAX_BODY_LEN}"
        )

    payload = bytearray()
    payload.append(target_group)
    payload.append(r)
    payload.append(g)
    payload.append(b)
    payload.append(ttl_ms & 0xFF)
    payload.append((ttl_ms >> 8) & 0xFF)
    payload.append(len(header_bytes))
    payload.extend(header_bytes)
    payload.append(len(body_bytes))
    payload.extend(body_bytes)

    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"TEXT_DISPLAY payload would be {len(payload)} bytes; "
            f"max {MAX_PAYLOAD_SIZE}"
        )

    hdr = _build_header(
        source_id, sequence, hop_count, MessageType.TEXT_DISPLAY, len(payload)
    )
    return hdr + bytes(payload)


# ---------------------------------------------------------------------------
# CLEAR_SCREEN (3-byte fixed payload)
# ---------------------------------------------------------------------------


def encode_clear_screen(
    *,
    source_id: int = BROADCAST_SOURCE_ID,
    sequence: int = 1,
    hop_count: int = 0,
    target_group: int = 0,
    clear_text: bool = True,
    clear_bitmap: bool = True,
) -> bytes:
    """Encode a CLEAR_SCREEN frame.

    The two flags clear independent layers - an author can drop the
    song title without disturbing the band logo bitmap underneath, for
    instance.
    """
    if not (0 <= target_group <= 0xFF):
        raise ValueError(f"target_group out of range: {target_group}")
    payload = bytes(
        (
            target_group,
            1 if clear_text else 0,
            1 if clear_bitmap else 0,
        )
    )
    hdr = _build_header(
        source_id, sequence, hop_count, MessageType.CLEAR_SCREEN, len(payload)
    )
    return hdr + payload
