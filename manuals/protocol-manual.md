---
title: "NocturNation protocol manual"
status: Draft
protocol_version: 0x02
firmware_version: "v0.6"
notion_url: https://www.notion.so/35ebd067740580378400ec3e0e8a0ca0
notion_id: 35ebd067740580378400ec3e0e8a0ca0
last_synced: 2026-07-03
sync_direction: bidirectional
---

# NocturNation protocol manual

> Normative specification of the NocturNation protocol: ESP-NOW transport, frame formats, class-and-group addressing, the PixMob infra-red encoding annex, channel discovery, the firmware non-volatile-storage schema, conformance requirements, and reference test vectors.

This is the implementer-facing document. If you are an operator setting up a venue, read the [user manual](user-manual.md) instead. If you are designing show plug-ins for the NocturNation firmware, read [developing-shows.md](../developing-shows.md). For visual reference alongside this spec, the [flow-diagrams document](flow-diagrams.md) has Mermaid renderings of the receive pipeline and class-and-group routing.

**Protocol version specified by this document**: `0x02`.
**Reference firmware version**: v0.6 (`include/firmware_version.h`).
**Reference encoder for the PixMob IR annex**: [jamesw343/PixMob_IR](https://github.com/jamesw343/PixMob_IR).

---

## Contents

1. [Scope and conventions](#1-scope-and-conventions)
2. [Wireless layer](#2-wireless-layer)
3. [Frame format](#3-frame-format)
4. [Class-and-group addressing](#4-class-and-group-addressing)
5. [Channel discovery](#5-channel-discovery)
6. [Heartbeat and liveness](#6-heartbeat-and-liveness)
7. [Conformance](#7-conformance)
8. [Annex A: PixMob infra-red encoding](#annex-a-pixmob-infra-red-encoding)
9. [Annex B: Non-volatile-storage schema](#annex-b-non-volatile-storage-schema)
10. [Annex C: Reference test vectors](#annex-c-reference-test-vectors)
11. [Annex D: Protocol version history](#annex-d-protocol-version-history)

---

## 1. Scope and conventions

### 1.1 Scope

This document specifies the wire-visible behaviour of a NocturNation node: every byte transmitted on the radio link, every byte transmitted on the infra-red link, and every byte stored in non-volatile storage that influences either. It does not specify firmware internals such as plug-in architecture, the audio analyser tuning, or the configuration-menu structure; those are operator-facing concerns described in the user manual and architectural concerns described in [docs/architecture.md](../architecture.md).

An implementation conforming to this document interoperates with the reference firmware over both the radio link and the infra-red link.

### 1.2 Normative language

Throughout this document, the words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are to be interpreted as defined in IETF RFC 2119 (Bradner, 1997).

### 1.3 Notation

- Hexadecimal values are prefixed `0x`. Byte ranges are inclusive on both ends.
- Multi-byte integer fields are **little-endian** unless explicitly stated otherwise.
- Bit numbering in bitfield diagrams is most-significant-bit first when read left-to-right; the lowest-numbered bit is the least significant.
- Field offsets are zero-based byte offsets from the start of the enclosing structure.

### 1.4 Versioning

Every frame begins with a two-byte magic prefix (`0x4E 0x4E`, ASCII "NN") followed by a one-byte `protocol_version` field. The value of `protocol_version` specified by this document is `0x02`. A receiver MUST validate the magic prefix first, then the version byte, discarding frames whose magic or version it does not recognise. Future revisions of the protocol MAY introduce new message types within the same version (using reserved opcodes) or MAY bump the version byte if a wire-incompatible change is required.

The protocol version is independent of the firmware version. Firmware version `v0.6` implements protocol version `0x02`.

### 1.5 Licence

This document is licensed under [Creative Commons Attribution-ShareAlike 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (CC BY-SA 4.0). Implementations are MIT-licensed (see the reference firmware's [LICENSE.code](../../LICENSE.code) file). Hardware designs derived from the reference platforms are CERN-OHL-S 2.0.

---

## 2. Wireless layer

### 2.1 Carrier

NocturNation operates on the 2.4 GHz ISM band using Espressif's **ESP-NOW** transport, which carries vendor-specific action frames in the IEEE 802.11 management category. ESP-NOW is connection-less and broadcast-friendly; the wireless medium is unencrypted and unauthenticated at this protocol version (Tier 0 security; see [security RFC](https://www.notion.so/358bd0677405817b8a60de0834511ce5) for the deferred Tier 1 path).

A NocturNation frame is encapsulated as the payload of one ESP-NOW vendor action frame. The destination address is the broadcast MAC `ff:ff:ff:ff:ff:ff`. Receivers process every frame that arrives on the current channel; no association is required.

ESP-NOW is the reference carrier for this version of the protocol. The frame format in [section 3](#3-frame-format) carries no ESP-NOW-specific fields, so the same frames could in principle be carried over another link such as Bluetooth LE or infra-red; other carriers are not specified by this document.

#### 2.1.1 PHY mode: Long Range (LR)

Added in Epic 15 (2026-06-27). The reference firmware configures the ESP-NOW radio in **Long Range** PHY mode fleet-wide (both Director and Lume). LR is Espressif's proprietary sub-1 Mb/s modulation on top of the same 2.4 GHz carrier; it trades peak throughput for sensitivity and interference resilience:

| Parameter | Standard 802.11 b/g/n | LR mode |
|---|---|---|
| Payload rate | 1..54 Mb/s | ~500 kb/s |
| Receiver sensitivity | ~-92 dBm | ~-105 dBm |
| Typical open-air range at 20 dBm TX | ~150 m | ~1 km |

Every deployed NocturNation device MUST use the same PHY mode: an LR transmitter is unreadable by a non-LR receiver on the same channel, and vice-versa. There is no per-frame indicator of the mode; a receiver simply sees noise if the transmitter is on the wrong PHY. Fleet-wide LR is the current baseline; a future protocol revision MAY permit mixed operation if a discovery mechanism is added.

Field time and airtime accounting are unaffected by LR — a single frame still occupies one ESP-NOW action frame regardless of PHY — but the on-air duration of each frame is roughly 3× longer, which the redundancy triple in [section 2.3](#23-redundancy) already accommodates. Operators SHOULD size Director-side beat/effect throughput against LR's lower peak rate; the reference firmware has been bench-validated at ~40 frames per second sustained on channel 11 with LR.

### 2.2 Channels

NocturNation uses three of the standard non-overlapping 2.4 GHz Wi-Fi channels. A Director MUST be configured on exactly one of these channels at any moment:

| Channel | Centre frequency | Role |
|---|---|---|
| 1 | 2412 MHz | Hobby / open community (default) |
| 6 | 2437 MHz | Advanced operator override |
| 11 | 2462 MHz | Show / commercial |

Channel 11 is suggested for high-density public deployments because it is least congested in venue environments dominated by 2.4 GHz Wi-Fi infrastructure on channels 1 and 6.

Lumes MAY be locked to a single channel or MAY auto-scan. See [section 5](#5-channel-discovery).

### 2.3 Redundancy

A Director transmitting a frame MUST send it **three** times in immediate succession on the same channel. The three transmissions carry identical bytes; in particular, they carry the **same** sequence number (see [section 3](#3-frame-format)). This redundancy absorbs the occasional ESP-NOW packet loss without requiring acknowledgement.

A receiver MUST deduplicate against a ring of at least sixteen recently-seen `(source_id, sequence_number)` pairs. A frame matching any entry in the ring MUST be dropped silently (no processing, no further forwarding). A frame not matching any entry MUST be processed and the pair MUST be appended to the ring, evicting the oldest entry if the ring is full.

### 2.4 Repeater behaviour

A Lume MAY operate as a **repeater**, in which case every accepted frame (one that passed deduplication) is retransmitted with the `hop_count` field incremented by one. A receiver MUST drop frames with `hop_count` greater than 3 to bound the relay topology. The default behaviour is no repeating.

### 2.5 No acknowledgement, no return path

NocturNation is unidirectional. A Lume never transmits a frame back to the Director; a bracelet never transmits anything at all. Director state (sequence numbers, mode, channel) is the single source of truth; Lumes derive their behaviour from received frames and from local configuration.

---

## 3. Frame format

### 3.1 Header

Every frame begins with an eight-byte header:

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `magic[0]` | 1 | Always `0x4E` (ASCII `N`) |
| 1 | `magic[1]` | 1 | Always `0x4E` (ASCII `N`) |
| 2 | `protocol_version` | 1 | Always `0x02` at this revision |
| 3 | `source_id` | 1 | Sender id, partitioned by range and channel - see [section 3.4](#34-source-identifier-partitioning). `0xFF` = broadcast / anonymous. |
| 4 | `sequence_number` | 1 | Wraps 1..255 in monotonic order per source; `0x00` indicates no sequencing |
| 5 | `hop_count` | 1 | 0 = original transmission; receiver MUST drop frames where hop_count > 3 |
| 6 | `message_type` | 1 | See [section 3.2](#32-message-types) |
| 7 | `payload_len` | 1 | Bytes of payload following the header |
| 8..N | `payload` | `payload_len` | Type-specific (see [section 3.3](#33-payloads)) |

`kHeaderSize = 8`. `kMaxFrameSize = 32`. `kMaxPayloadSize = kMaxFrameSize - kHeaderSize = 24`.

The two-byte magic prefix (`0x4E 0x4E`, ASCII "NN") discriminates NocturNation traffic from other ESP-NOW users sharing the same 2.4 GHz channel - a real concern at event-density deployments (EMF, festivals) where many devices broadcast on the same band. A receiver MUST validate the magic prefix as the very first check; frames whose `magic[0..1]` is not `0x4E 0x4E` MUST be silently discarded before any further header parsing.

A receiver MUST verify that `payload_len` matches the expected length for the given `message_type` (see [section 3.3](#33-payloads)) and SHOULD silently discard frames whose `payload_len` is inconsistent.

### 3.2 Message types

| Code | Name | Payload size | Direction |
|---:|---|---:|---|
| `0x00` | `HEARTBEAT` | 9 | Director to all |
| `0x03` | `LIGHT_PULSE` | 9 | Director to all |
| `0x06` | `LIGHT_WASH` | 16 | Director to all (capable Lumes act on it; pulse-only Lumes drop) |
| `0x07` | `LIGHT_WASH_END` | 3 | Director to all (capable Lumes act on it; pulse-only Lumes drop) |
| `0x08` | `LIGHT_WASH_PULSE` | 9 | Director to all (only Lumes currently washing act on it; everyone else drops) |
| `0x09` | `TEXT_DISPLAY` | 8..200 | Director to all (Lumes with `DisplayText` capability render; others drop) |
| `0x0A` | `BITMAP_HEADER` | 37 | Director to all (Lumes with `DisplayBitmap` capability stage a receive buffer; others drop) |
| `0x0B` | `BITMAP_PLANE` | 5..242 | Director to all (Lumes with `DisplayBitmap` capability accumulate plane bytes; others drop) |
| `0x0C` | `CLEAR_SCREEN` | 3 | Director to all (Lumes with `DisplayText` or `DisplayBitmap` capability clear the corresponding surface; others drop) |
| `0xFF` | `EXTENSION` | variable | Reserved for future use |

All other code points are reserved. A receiver MUST treat any unrecognised `message_type` as a request to silently discard the frame; this is the forward-compatibility rule that lets a future protocol revision introduce new types without breaking older receivers.

A receiver MUST honour at minimum: `HEARTBEAT`, `LIGHT_PULSE`. A receiver MAY honour `EXTENSION` and future code points when defined. A receiver that declares itself **wash-capable** (per its `BindingCapabilities` surface; see [developing-shows.md / capability design doc](../lume-capabilities-design.md)) MUST honour `LIGHT_WASH`, `LIGHT_WASH_END`, and `LIGHT_WASH_PULSE`; a receiver that is *not* wash-capable MUST silently drop these three types. A receiver that declares `Capability::DisplayText` MUST honour `TEXT_DISPLAY` and the text half of `CLEAR_SCREEN`; a receiver that declares `Capability::DisplayBitmap` MUST honour `BITMAP_HEADER`, `BITMAP_PLANE`, and the bitmap half of `CLEAR_SCREEN`. A receiver without the relevant display capability MUST silently drop the corresponding type at the earliest gate (before payload decode) — for a headless bracelet-only Lume this means zero decode work on display traffic.

### 3.3 Payloads

#### 3.3.1 `HEARTBEAT` (`0x00`)

The Director's nine-byte liveness frame. Carries a monotonic tick plus an optional wall-clock anchor for Tier 3 receivers (signed-cert validity windows).

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `tick` | 4 LE | Monotonic Director uptime tick, units implementation-defined; wraps on `u32` overflow. Receivers MUST NOT assume a fixed unit and SHOULD use it only for ordering and liveness, not absolute time. |
| 4 | `days_since_2026` | 2 LE | Day count since 2026-01-01. Director sets to `0x0000` if it has no wall-clock source. |
| 6 | `centiseconds_today` | 3 LE | Centiseconds since local midnight, range 0..8,639,999. Director sets to `0x000000` if it has no wall-clock source. |

`payload_len == 9`. The authoritative byte-for-byte layout is enforced by `test_heartbeat_wire_format_byte_for_byte` in [`test/test_espnow_frame/test_main.cpp`](../../test/test_espnow_frame/test_main.cpp). See [section 6](#6-heartbeat-and-liveness) for emission cadence and liveness semantics; see [annex C.2](#c2-esp-now-heartbeat-frame) for a worked frame.

#### 3.3.2 `LIGHT_PULSE` (`0x03`)

> Renamed from `LIGHT_COMMAND` in Epic 6C Phase C (2026-05-31). Wire byte and payload are unchanged - this is a name-only refactor that frees the "LIGHT" prefix for the upcoming `LIGHT_WASH` family (`0x06`/`0x07`/`0x08`). Old name retained here for cross-reference.

The most-emitted message type; carries every render fire on the system.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_class` | 1 | See [section 4](#4-class-and-group-addressing); 0 = all classes, 1 = Light, 2 = Screen, 3 = MultiLedScreen |
| 1 | `target_group` | 1 | 0 = broadcast within class; 1..255 = specific group (PixMob receivers further constrain to 1..31) |
| 2 | `r` | 1 | Red 0..255 |
| 3 | `g` | 1 | Green 0..255 |
| 4 | `b` | 1 | Blue 0..255 |
| 5 | `attack` | 1 | Envelope attack stage; PixMob `Time` enum index 0..7 (see [annex A.3](#a3-time-and-chance-enumerations)) |
| 6 | `sustain` | 1 | Envelope sustain stage; PixMob `Time` enum index 0..7 |
| 7 | `release` | 1 | Envelope release stage; PixMob `Time` enum index 0..7 |
| 8 | `chance` | 1 | Probability gate; PixMob `Chance` enum index 0..7 (see [annex A.3](#a3-time-and-chance-enumerations)) |

A receiver whose configured `device_class` matches `target_class` (or `target_class == 0x00`), and whose configured `group` matches `target_group` (or `target_group == 0x00`), MUST render this command according to its own device class. See [section 4](#4-class-and-group-addressing) for the full routing semantics.

#### 3.3.3 `LIGHT_WASH` (`0x06`)

> Added in Epic 6C Phase D (2026-05-31). The wash-family primitive that lets a capable Lume hold a persistent background colour with optional cosine-eased two-colour drift, on top of which a `LIGHT_PULSE` can overlay. The semantic contract is in [`lume-capabilities-design.md`](../lume-capabilities-design.md); this section is the wire-format normative reference.

Wash baseline for capable Lumes. Cosine-eased ping-pong between `r1/g1/b1` and `r2/g2/b2` over `cycle_ms`; `cycle_ms = 0` holds `r1/g1/b1` only.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_class` | 1 | Per [section 4](#4-class-and-group-addressing); `0` = all classes. |
| 1 | `target_group` | 1 | `0` = broadcast within class; `1..255` = specific group. |
| 2 | `r1` | 1 | Start colour red 0..255. |
| 3 | `g1` | 1 | Start colour green 0..255. |
| 4 | `b1` | 1 | Start colour blue 0..255. |
| 5 | `r2` | 1 | End colour red 0..255. Ignored by the renderer when `cycle_ms == 0`, but the byte is still on the wire. |
| 6 | `g2` | 1 | End colour green 0..255. |
| 7 | `b2` | 1 | End colour blue 0..255. |
| 8 | `attack` | 1 | Ramp time from current rendered colour into the wash baseline. Units: **100 ms** (range 0..25.5 s). |
| 9 | `release` | 1 | Default fade-out time when the wash ends (TTL expiry or superseded by another `LIGHT_WASH`). Units: **100 ms**. May be overridden by a `LIGHT_WASH_END.release_time` for explicit cancellation. |
| 10 | `intensity` | 1 | Brightness scalar 0..255 applied to the wash baseline before any pulse overlay. |
| 11 | `cycle_ms` | 2 LE | One full A↔B↔A oscillation in milliseconds. `0` = no cycle, hold `r1/g1/b1`. |
| 13 | `ttl_seconds` | 2 LE | Time-to-live in seconds. `0` = infinite (held until `LIGHT_WASH_END` or a superseding `LIGHT_WASH`). |
| 15 | `pulse_response` | 1 | `0` = ignore inbound `LIGHT_PULSE` while washing (wash holds untouched); `1` = accept `LIGHT_PULSE` as additive overlay on the live wash baseline. |

`payload_len == 16`. A wash-capable Lume MUST honour this command per the routing semantics in [section 4](#4-class-and-group-addressing) and the renderer contract in [`lume-capabilities-design.md` §4.1](../lume-capabilities-design.md). A pulse-only Lume MUST silently drop this command.

#### 3.3.4 `LIGHT_WASH_END` (`0x07`)

> Added in Epic 6C Phase D. Explicit cancel of an active wash, with operator-chosen fade time that overrides the wash's own `release`.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_class` | 1 | Same routing as `LIGHT_WASH`. |
| 1 | `target_group` | 1 | Same. |
| 2 | `release_time` | 1 | Fade from the instantaneous wash colour to black over this duration, then exit wash mode. Units: **100 ms**. Overrides the active wash's own `release` field. |

`payload_len == 3`. A wash-capable Lume with an active wash MUST fade to black over `release_time` and then exit wash mode (resuming regular `LIGHT_PULSE` rendering against a black baseline). A wash-capable Lume with *no* active wash MUST silently drop this command. A pulse-only Lume MUST silently drop this command.

#### 3.3.5 `LIGHT_WASH_PULSE` (`0x08`)

> Added in Epic 6C Phase D. Same payload shape as `LIGHT_PULSE` (9 bytes); differs only in dispatch semantics — fires only on Lumes currently in wash state.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0..8 | (identical to [`LIGHT_PULSE`](#332-light_pulse-0x03)) | 9 | Same wire layout as `LIGHT_PULSE`. |

`payload_len == 9`. A wash-capable Lume with an **active wash** MUST render this command as an additive overlay on the wash baseline (regardless of the wash's `pulse_response` flag). A wash-capable Lume with *no* active wash MUST silently drop this command. A pulse-only Lume MUST silently drop this command. The separation from `LIGHT_PULSE` keeps the addressing dimensions orthogonal: `LIGHT_PULSE` fires on every Lume in the target class+group; `LIGHT_WASH_PULSE` fires only on the washing subset.

#### 3.3.6 `TEXT_DISPLAY` (`0x09`)

> Added in Epic 13 (2026-06-14). Header and body strings for the Lume's screen surface. UTF-8; the reference firmware uses the bundled Ctx font which is Latin-only, so authors targeting audience-worn badges SHOULD romanise non-Latin content at the cue-file layer (see `developing-shows.md`).

The message type itself denotes the surface (`DeviceClass::Display` + `Capability::DisplayText`); there is no `target_class` byte on the wire — a receiver that has already declined the frame at the capability gate never reaches this payload. `target_group` remains for per-Lume addressing within the Display class (group 0 = all Display-class Lumes).

`header` and `body` are independent length-prefixed strings; either may be empty (length 0). Empty header means "no header line"; empty body means "no body text". Both empty is legal and acts as a no-op on a surface with existing content (the fields do not overwrite what's already displayed unless explicitly non-empty).

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_group` | 1 | 0 = all Display-class Lumes; 1..255 = specific group. |
| 1 | `r` | 1 | Foreground text red 0..255. |
| 2 | `g` | 1 | Foreground text green 0..255. |
| 3 | `b` | 1 | Foreground text blue 0..255. |
| 4 | `ttl_ms` | 2 LE | 0 = sticky (persists until `CLEAR_SCREEN` or a superseding `TEXT_DISPLAY`); non-zero = auto-clear after this many milliseconds. |
| 6 | `header_len` | 1 | 0..64. |
| 7 | `header_bytes` | `header_len` | UTF-8 bytes. |
| 7+`header_len` | `body_len` | 1 | 0..128. |
| 8+`header_len` | `body_bytes` | `body_len` | UTF-8 bytes. |

`payload_len` = 8 (both strings empty) .. 200 (both strings at max). A Lume declaring `Capability::DisplayText` MUST render the header and body to its screen surface honouring the `ttl_ms` semantics. A Lume declaring `Capability::DisplayText` MUST also honour a `CLEAR_SCREEN` frame's `clear_text` field to blank the text surface without affecting an active bitmap.

The maximum lengths (64 header / 128 body) are wire-format constants; a Lume MAY truncate to a lower rendering budget dictated by its own screen dimensions and font.

#### 3.3.7 `BITMAP_HEADER` (`0x0A`)

> Added in Epic 13. Framing for a bitmap render. One `BITMAP_HEADER` precedes N `BITMAP_PLANE` frames; the receiver stages a buffer sized to `ceil(width * height / 8) * plane_count` bytes, accumulates plane bytes, then verifies the checksum before committing to the render surface.

The message type denotes the surface (`DeviceClass::Display` + `Capability::DisplayBitmap`) — no `target_class` byte.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_group` | 1 | 0 = all Display-class Lumes; 1..255 = specific group. |
| 1 | `width` | 1 | 1..64 pixels. |
| 2 | `height` | 1 | 1..64 pixels. |
| 3 | `plane_count` | 1 | 1..8. Bits per rendered pixel = `plane_count`. |
| 4 | `colours` | 24 | Eight RGB triplets. Only the first `plane_count` slots are meaningful; the rest are ignored but present on the wire for a fixed layout. Each plane's `1` bits render in the corresponding colour. |
| 28 | `fit` | 1 | 0 = ACTUAL (plot at native dimensions, top-left origin); 1 = FIT (preserve aspect, scale to display largest dimension); 2 = ZOOM (preserve aspect, fill display, crop overflow). |
| 29 | `zoom_pct` | 1 | Multiplier on the FIT/ZOOM scale factor. 100 = baseline; 50 = half; 200 = 2x. Ignored when `fit == 0`. |
| 30 | `overwrite` | 1 | 0 = ADDITIVE (compose onto whatever is currently on the surface); 1 = REPLACE (clear surface at plane 0 of this header set). |
| 31 | `checksum` | 4 LE | CRC32 over all plane bytes concatenated in plane-index order. |
| 35 | `ttl_ms` | 2 LE | 0 = sticky; non-zero = auto-clear after this many milliseconds. |

`payload_len == 37`. A Lume declaring `Capability::DisplayBitmap` MUST allocate a staging buffer and MUST NOT commit the bitmap until every plane's bytes have arrived AND the concatenation matches `checksum`. A Lume MAY drop the entire staging buffer on receipt of a subsequent `BITMAP_HEADER` for the same `target_group` before the previous set completes; wire-format retries are not defined.

#### 3.3.8 `BITMAP_PLANE` (`0x0B`)

> Added in Epic 13. Chunked pixel bytes for one plane of a bitmap. Multiple `BITMAP_PLANE` frames per plane are permitted and distinguished by `byte_offset` into that plane's byte stream.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_group` | 1 | Same routing as the preceding `BITMAP_HEADER`. |
| 1 | `plane_index` | 1 | 0..(`plane_count` - 1) per the preceding `BITMAP_HEADER`. |
| 2 | `byte_offset` | 2 LE | Offset into this plane's pixel byte stream where `data_bytes` begins. |
| 4 | `data_len` | 1 | 0..237. Number of payload bytes in `data_bytes`. |
| 5 | `data_bytes` | `data_len` | Raw plane bytes; bit-packing is row-major, MSB-first within each byte. |

`payload_len` = 5 (`data_len == 0`) .. 242 (`data_len == 237`). A receiver MUST accumulate `data_bytes` at the plane's base offset + `byte_offset` into the staging buffer allocated by the preceding `BITMAP_HEADER`. A `BITMAP_PLANE` frame arriving without a matching `BITMAP_HEADER` (or after the header's staging buffer has been committed / abandoned) MUST be silently discarded.

The 237-byte data cap is the wire-format maximum; encoders MAY chunk smaller for latency-sensitive shows.

#### 3.3.9 `CLEAR_SCREEN` (`0x0C`)

> Added in Epic 13. Per-surface clear on the Lume's screen.

| Offset | Field | Size | Description |
|---:|---|---:|---|
| 0 | `target_group` | 1 | 0 = all Display-class Lumes; 1..255 = specific group. |
| 1 | `clear_text` | 1 | 0 = leave text surface untouched; non-zero = clear text surface. |
| 2 | `clear_bitmap` | 1 | 0 = leave bitmap surface untouched; non-zero = clear bitmap surface. |

`payload_len == 3`. `clear_text` and `clear_bitmap` are independent so an author can drop the song title without disturbing the band logo. A Lume declaring `Capability::DisplayText` MUST honour `clear_text`; a Lume declaring `Capability::DisplayBitmap` MUST honour `clear_bitmap`; a Lume without the relevant capability MUST silently drop the corresponding half (a Lume with neither MUST drop the entire frame at the capability gate).

Both flags zero is legal and acts as a no-op; the reference encoder emits `clear_text=1, clear_bitmap=1` for a full-screen clear.

#### 3.3.10 `EXTENSION` (`0xFF`)

Reserved for future use. A receiver MUST silently discard frames of this type at protocol version `0x02`.

### 3.4 Source identifier partitioning

The `source_id` field at offset 3 of the frame header is partitioned by range to support channel-specific access control on the broadcast side and Trust-On-First-Use locking on the receive side. The partition is a convention layered on top of the existing one-byte field; no wire-format change.

| Range | Slots | Use | Director allocation rule | Default channel binding |
|---|---:|---|---|---|
| `0x00 - 0x3F` | 64 | Community / hobby | Stable per device: pick a random ID in this range at first boot, persist to NVS, reuse on subsequent boots. | Channel 1. |
| `0x40 - 0xFE` | 191 | Performance mode | Random per boot: pick a fresh ID in this range at every boot; listen-before-broadcast. | Channel 11. |
| `0xFF` | 1 | Broadcast / anonymous | Used by senders that intentionally identify as anonymous, or as a wildcard in receiver-side filters. | Any channel. |

**Channel 6** is an advanced operator override and is not constrained by this partition. Operators configuring a Director on channel 6 SHOULD pick a Performance-range source_id, but a Lume on channel 6 MUST accept any source_id (channel 6 is permissive by design).

**Director-side rules:**

- A Director MUST allocate its `source_id` from the range matching its configured channel: community range (`0x00-0x3F`) on channel 1; Performance range (`0x40-0xFE`) on channel 11.
- On channel 1, the chosen ID MUST be persisted to NVS at first boot and MUST be reused on subsequent boots. The reference firmware uses the NVS key `mst_src_id` (see [annex B](#annex-b-non-volatile-storage-schema)).
- On channel 11, the chosen ID MUST be regenerated at every boot and MUST NOT be persisted. The Director MUST listen for at least one second before its first transmission to detect a colliding ID. If a `HEARTBEAT` matching its chosen ID arrives during the listen window, the Director MUST re-roll and listen again. After three consecutive collisions the Director MAY proceed with the third pick and SHOULD log a warning; the probability of three consecutive collisions with 191 slots and a small number of concurrent Directors is operationally negligible.
- A Director SHOULD display its `source_id` on its operator UI so the operator can verify which ID the audience is locking to.

**Lume-side rules (Trust-On-First-Use):**

- A Lume MUST lock to the `source_id` of the first valid frame it receives on a channel after scan or rescan. Subsequent frames whose `source_id` differs from the locked value MUST be silently discarded. (Locking on any valid frame rather than `HEARTBEAT` specifically accommodates Lumes that join during active music: the Director's heartbeat is suppressed by skip-if-recent per [section 6.1](#61-director-heartbeat) while `LIGHT_PULSE` frames flow, so a HEARTBEAT-only rule would leave a mid-song Lume idle for the duration of a song.)
- A Lume on channel 11 MUST consider only Performance-range source_ids (`0x40-0xFE`) eligible for TOFU lock. A frame carrying a community-range source_id on channel 11 MUST be silently discarded without locking. This defends Lumes against a misconfigured Director announcing on the wrong channel.
- A Lume on channel 1 MUST accept any non-broadcast source_id for TOFU lock; channel 1 is the community-permissive channel by design.
- A Lume MUST release its TOFU lock and resume scanning if no frame from the locked `source_id` has been received for `kRescanMs` milliseconds. The reference firmware uses `kRescanMs = 10000` (ten seconds), shared with the channel re-scan threshold ([section 5.4](#54-lume---re-scan-on-signal-loss)).
- A Lume MAY expose a "Rescan" operator action that releases the TOFU lock on demand.
- A Lume SHOULD display the locked `source_id` on its operator UI so the audience can verify which Director it is locked to.

**Tildagon Director-mode constraint (forward-looking):** when Director mode is added to the Tildagon (planned for a later Epic), a Tildagon-class Director MUST NOT broadcast on channel 11; Tildagon Directors are restricted to channel 1 (community range) for transmission. The Tildagon is a community badge distributed at scale; keeping it off channel 11 as a transmitter protects the integrity of curated Performance Mode shows at events like EMF. This restriction is conservative and MAY be revisited in a future protocol revision once the access control model has matured in deployment. The Tildagon remains free to *receive* on channel 11 in Lume mode, with TOFU and cross-range filtering applied as for any other Lume.

---

## 4. Class-and-group addressing

### 4.1 Device classes

Every NocturNation receiver advertises a **device class** in the range `0x00..0xFF`. The class identifies the kind of device the receiver presents on the wire.

| Code | Name | Description |
|---:|---|---|
| `0x00` | `All` | Addressing wildcard; never returned by a receiver, only used as a `target_class` for global broadcast |
| `0x01` | `Light` | Discrete-LED bracelet or wristband; e.g. PixMob Aurora |
| `0x02` | `Screen` | Framebuffer-bearing device; e.g. the Stick's LCD |
| `0x03` | `MultiLedScreen` | Device with both discrete LEDs and a framebuffer; e.g. the Tildagon (Epic 5) |
| `0x04..0xFF` | reserved | Future use |

A receiver MUST advertise a single class. A receiver running multiple bindings (the reference firmware running both a screen display binding and a PixMob infra-red binding) MAY treat each binding as a separate logical receiver, each with its own class and group.

### 4.2 Group filtering

Every NocturNation receiver also advertises a **group** in the range `0x00..0xFF`. The group is a one-byte filter; multiple receivers MAY share a group. Bracelet receivers in the PixMob class further constrain the group to the five-bit subset `0x00..0x1F` because the on-wire PixMob protocol carries only five bits of group; see [annex A.1](#a1-frame-format).

A receiver MUST accept a `LIGHT_PULSE` if and only if:

- `target_class == 0x00` OR `target_class == receiver.class`, AND
- `target_group == 0x00` OR `target_group == receiver.group`.

`target_group == 0x00` is the **broadcast group**: every receiver MUST accept it regardless of its own configured group, including a receiver whose configured group is itself `0x00`. The broadcast group is the canonical "address everyone in this class" form and is the default routing for `render_fx` calls on the reference Director.

A receiver whose configured group is `0x00` is treated as "in no specific group". It MUST accept the broadcast group (`target_group == 0x00`) but MUST NOT accept any non-zero `target_group`. This mirrors the way a PixMob bracelet whose factory-programmed group is 0 only responds to the broadcast group on its infra-red link.

A receiver SHOULD therefore advertise a non-zero group in deployment. A receiver whose group is intentionally `0x00` is opting out of all per-group fan-out routing and only sees broadcasts.

### 4.3 Worked routing examples

| `target_class` | `target_group` | Routes to |
|---:|---:|---|
| `0x00` | `0x00` | Every receiver of every class - global broadcast |
| `0x00` | `0x07` | Every receiver in group 7, regardless of class |
| `0x01` | `0x00` | Every Light-class receiver in any group |
| `0x01` | `0x07` | Light-class receivers configured for group 7 only |
| `0x02` | `0x00` | Every Screen-class receiver - typically the operator's own LCD |
| `0x03` | `0x01` | Every MultiLedScreen-class receiver (Tildagon, Epic 5) in group 1 |

### 4.4 Director-side dispatch

The reference firmware's dispatch function `dispatch_output_class_group` (`src/dal/dal.cpp`) fans every render call out to three sinks:

1. **ESP-NOW broadcast** - always, regardless of `target_class`. Every Lume on the channel sees the frame.
2. **Local infra-red transmitter** - only when `target_class` is `0x00` (All) or `0x01` (Light). This is the Director's habit of treating itself as one of its own Lumes (the "loopback"). Exactly one IR frame is sent per dispatch call.
3. **Local screen pulse** - only when `target_class` is `0x00` (All) or `0x02` (Screen). Drives the LCD pulse animation.

This is dispatch-side behaviour and is not visible to the wire; a third-party Director implementation MAY adopt the same loopback or omit it.

---

## 5. Channel discovery

### 5.1 Director

The Director is configured for a fixed channel (1, 6, or 11) via non-volatile storage (`mst_chan`; see [annex B](#annex-b-non-volatile-storage-schema)). It MUST NOT change channels during a deployment. The Director's `source_id` allocation rule depends on the configured channel; see [section 3.4](#34-source-identifier-partitioning).

### 5.2 Lume - locked mode

A Lume configured with `slv_chan ∈ {1, 6, 11}` MUST set its Wi-Fi to that channel and remain there.

### 5.3 Lume - auto-scan mode

A Lume configured with `slv_chan == 0x00` MUST perform an auto-scan, defined as the following sequence:

1. Set channel to 11. Listen for up to two seconds for any NocturNation frame with valid magic and `protocol_version`.
2. If a frame is received, lock to channel 11 and exit scan.
3. Otherwise, set channel to 1. Listen for up to two seconds.
4. If a frame is received, lock to channel 1 and exit scan.
5. Otherwise, set channel to 6. Listen for up to two seconds.
6. If a frame is received, lock to channel 6 and exit scan.
7. Otherwise, repeat from step 1.

The scan order is 11 → 1 → 6 → repeat. Channel 11 is checked first because it is the suggested show channel and is presumed higher priority; channel 1 (hobby / open community) is the natural fallback; channel 6 (advanced operator override) is checked last because it is the least likely to carry traffic. Worst-case discovery latency from a cold start is six seconds.

### 5.4 Lume - re-scan on signal loss

A Lume that was originally configured for auto-scan (`slv_chan == 0x00`) and has subsequently locked to a channel SHOULD re-enter auto-scan if it loses traffic for longer than `kRescanMs` milliseconds. The reference firmware uses `kRescanMs = 10000` (ten seconds).

The re-scan threshold is **deliberately decoupled** from the NO SIGNAL display threshold (`kNoSignalGapMs = 3000`, see [section 6.2](#62-receiver-liveness-check)). NO SIGNAL displays quickly so the operator sees the outage; re-scan waits longer because most signal losses are transient (Director rebooting, brief congestion, a person walking between antennas) and a 7-second window of "stay on the current channel" is much more likely to recover the existing Director than a multi-channel hunt.

A Lume explicitly locked to a channel by operator configuration (`slv_chan ∈ {1, 6, 11}`) MUST NOT re-enter auto-scan on signal loss. The operator chose that channel deliberately; the Lume MUST respect that choice and continue listening on the configured channel indefinitely. NO SIGNAL still displays for the operator's benefit, but no behavioural change follows.

---

## 6. Heartbeat and liveness

### 6.1 Director heartbeat

The Director MUST emit `HEARTBEAT` frames at no slower than 1 Hz when there is no other traffic. The Director MAY suppress a heartbeat if it has transmitted any other frame within the heartbeat period; this is the "skip-if-recent" rule and minimises duty cycle during active music.

The heartbeat carries a nine-byte payload (`payload_len == 9`) per [section 3.3.1](#331-heartbeat-0x00): a monotonic `tick` plus an optional wall-clock anchor in `days_since_2026` and `centiseconds_today`. Directors without a wall-clock source MUST set the two date/time fields to zero; receivers that need wall-clock validity (Tier 3) MUST treat all-zero date/time as "unknown" and reject cert checks accordingly. The frame's primary on-wire purpose is liveness; the wall-clock anchor is a piggyback.

### 6.2 Receiver liveness check

A receiver MUST consider the Director alive whenever it has received any frame within the last `kNoSignalGapMs` milliseconds. The reference firmware uses `kNoSignalGapMs = 3000` (three times the maximum heartbeat period).

A receiver that detects Director loss SHOULD indicate this clearly to a local operator (the reference firmware shows NO SIGNAL on the LCD). A receiver MUST NOT promote itself to Director, MUST NOT improvise a local effect that imitates Director output, and MUST NOT begin transmitting any NocturNation frames.

A receiver that detects Director return (the first received frame after a NO SIGNAL state) MUST resume normal operation immediately.

### 6.3 No reverse heartbeat

NocturNation has no Lume-to-Director heartbeat. A Director has no on-wire knowledge of which Lumes are alive; the operator visually checks each Lume's NO SIGNAL indicator.

---

## 7. Conformance

### 7.1 Receiver MUST honour

A conforming receiver MUST honour the following:

- The magic prefix check (`0x4E 0x4E` at offset 0..1) as the very first inbound validation. Frames failing this check MUST be silently discarded with no further processing.
- The Long Range (LR) PHY mode configuration on the ESP-NOW radio ([section 2.1.1](#211-phy-mode-long-range-lr)). A receiver on a different PHY sees noise on the carrier and cannot interoperate.
- The frame header layout and validation in [section 3.1](#31-header) and [section 3.2](#32-message-types).
- Deduplication on `(source_id, sequence_number)` against a ring of at least sixteen entries ([section 2.3](#23-redundancy)).
- The hop-count limit of 3 ([section 2.3](#23-redundancy)).
- The class-and-group routing rules in [section 4.2](#42-group-filtering).
- The `LIGHT_PULSE` payload semantics: RGB triplet, attack/sustain/release envelope stages, chance gate.
- The protocol-version validation rule in [section 1.4](#14-versioning).
- Trust-On-First-Use locking to the `source_id` of the first valid frame after scan or rescan, and silent discard of subsequent frames whose `source_id` differs from the locked value ([section 3.4](#34-source-identifier-partitioning)).
- Cross-range filtering on channel 11: silent discard of frames whose `source_id` is outside the Performance range (`0x40-0xFE`), without TOFU lock ([section 3.4](#34-source-identifier-partitioning)).

### 7.2 Receiver SHOULD honour

A conforming receiver SHOULD honour:

- The NO SIGNAL liveness behaviour in [section 6.2](#62-receiver-liveness-check).
- Channel auto-scan if it offers the capability ([section 5.3](#53-lume---auto-scan-mode)).
- The `HEARTBEAT` wall-clock fields if it needs wall-clock time (Tier 3 cert validity); otherwise the date/time fields MAY be ignored.

### 7.3 Receiver MAY honour

A conforming receiver MAY:

- Operate as a repeater ([section 2.4](#24-repeater-behaviour)).
- Implement screen-class rendering for `target_class == 0x02` frames.
- Decode the `HEARTBEAT.tick` field for liveness diagnostics beyond the binary "received within window" check.

### 7.4 Receiver MUST NOT

A conforming receiver MUST NOT:

- Auto-promote to Director on Director loss ([section 6.2](#62-receiver-liveness-check)).
- Transmit any NocturNation frame other than to forward an accepted frame as a repeater ([section 2.4](#24-repeater-behaviour)) or to render the local infra-red representation of a `LIGHT_PULSE` ([annex A](#annex-a-pixmob-infra-red-encoding) for the PixMob case).
- Process frames whose `protocol_version` does not match a recognised version.

### 7.5 Director MUST honour

A conforming Director MUST honour:

- The Long Range (LR) PHY mode configuration on the ESP-NOW radio ([section 2.1.1](#211-phy-mode-long-range-lr)). Fleet-wide LR is required — a Director transmitting on standard PHY is invisible to LR-configured Lumes and vice-versa.
- Three-times redundant transmission with identical sequence numbers ([section 2.3](#23-redundancy)).
- The heartbeat rule and skip-if-recent suppression ([section 6.1](#61-director-heartbeat)).
- The protocol-version byte at offset 0 of every frame.
- Channel fixity for the duration of a deployment ([section 5.1](#51-director)).
- `source_id` allocation from the range matching the configured channel ([section 3.4](#34-source-identifier-partitioning)).
- Listen-before-broadcast on channel 11: at least one second of receive-only listening before the first transmission, with re-roll on detected collision ([section 3.4](#34-source-identifier-partitioning)).

---

## Annex A: PixMob infra-red encoding

This annex describes the on-wire infra-red encoding for PixMob Aurora bracelets, derived from upstream reverse-engineering work by [James Wilson (jamesw343)](https://github.com/jamesw343/PixMob_IR). The NocturNation firmware parity-tests every byte of every transmitted infra-red frame against a Python reference encoder maintained against the upstream; conformance to that upstream is the load-bearing invariant.

A receiver implementing this annex interoperates with PixMob Aurora bracelets. Other PixMob product lines MAY use compatible encodings but have not been verified by this project.

### A.1 Frame format

The single-colour PixMob infra-red frame is **nine bytes**:

| Offset | Field | Description |
|---:|---|---|
| 0 | `sync_byte` | Always `0x80` |
| 1 | `checksum` | `ENCODING_MAP[(checksum_sum >> 2) & 0x3F]`; see [A.2](#a2-checksum) |
| 2 | `type_and_on_start` | `(type << 1) \| on_start`; single-colour fire uses `type == 0` and `on_start == 0`, giving `0x00` |
| 3 | `g_val_6bit` | `(g & 0xFF) >> 2`; high six bits of the eight-bit green channel |
| 4 | `r_val_6bit` | `(r & 0xFF) >> 2`; high six bits of red |
| 5 | `b_val_6bit` | `(b & 0xFF) >> 2`; high six bits of blue |
| 6 | `attack_and_chance` | `(attack << 3) \| (chance & 0x07)` |
| 7 | `release_and_sustain` | `(release << 3) \| (sustain & 0x07)` |
| 8 | `restrict_group_id` | `restrict_group_id & 0x1F`; 0 = broadcast, 1..31 = group filter |

The bracelet inspects byte 8 to decide whether to render. If `restrict_group_id == 0`, the bracelet renders unconditionally. If `restrict_group_id != 0`, the bracelet renders only when its own factory-assigned group matches.

Each byte is then mapped through the `ENCODING_MAP` lookup table (defined upstream in `jamesw343/PixMob_IR`) before being transmitted as an infra-red pulse train at the bracelet's expected carrier frequency. See the upstream repository for the transmit-side encoding pulse timing.

### A.2 Checksum

The checksum at offset 1 is computed as follows. Let `S` be the unsigned eight-bit sum of bytes at offsets 2 through 8 (seven bytes). The checksum byte is:

```
checksum = ENCODING_MAP[(S >> 2) & 0x3F]
```

A receiver MUST verify this checksum and discard frames whose computed value does not match. Note that the high two bits of the sum are deliberately discarded; the protocol does not require a strong integrity check, only enough to reject obvious bit-flips.

### A.3 `Time` and `Chance` enumerations

The three-bit `attack`, `sustain`, and `release` fields each index a `Time` enumeration:

| Index | Symbolic name | Duration |
|---:|---|---:|
| 0 | `T_0_MS` | 0 ms |
| 1 | `T_32_MS` | 32 ms |
| 2 | `T_96_MS` | 96 ms |
| 3 | `T_192_MS` | 192 ms |
| 4 | `T_480_MS` | 480 ms |
| 5 | `T_960_MS` | 960 ms |
| 6 | `T_2400_MS` | 2400 ms |
| 7 | `T_3840_MS` | 3840 ms |

The three-bit `chance` field indexes a `Chance` enumeration. Each value is a probability that an individual bracelet rolls **for itself** when the command arrives; the dice are independent across bracelets:

| Index | Symbolic name | Probability |
|---:|---|---:|
| 0 | `CHANCE_100` | 100% |
| 1 | `CHANCE_88` | 88% |
| 2 | `CHANCE_67` | 67% |
| 3 | `CHANCE_50` | 50% |
| 4 | `CHANCE_32` | 32% |
| 5 | `CHANCE_16` | 16% |
| 6 | `CHANCE_10` | 10% |
| 7 | `CHANCE_4` | 4% |

The envelope semantics on the bracelet are: ramp up to peak brightness over `attack` milliseconds; hold at peak for `sustain` milliseconds; ramp down to zero over `release` milliseconds. Total visible duration is the sum.

### A.4 Reference encoder

The authoritative encoder for this annex is [`jamesw343/PixMob_IR`](https://github.com/jamesw343/PixMob_IR). NocturNation's `test_pixmob_parity` test suite (`test/test_pixmob_parity/`) regenerates canonical byte sequences from the Python upstream and compares against the firmware's C++ encoder. Any disagreement is a defect in the C++ encoder, not in the upstream.

When generating new reference vectors for a protocol-level test, an implementer SHOULD run the upstream Python encoder and capture its byte output; do not bootstrap test fixtures from the C++ side.

---

## Annex B: Non-volatile-storage schema

This annex documents the keys that the reference firmware persists in non-volatile storage on the ESP32. It is informative; a third-party implementation MAY use any persistence layout it likes, provided the wire-visible behaviour conforms to the rest of this manual.

### B.1 Namespace

All keys live in a single namespace named `noct`. The reference firmware uses Espressif's `nvs_flash` library.

### B.2 Keys

| Key | Type | Default | Range | Purpose |
|---|---|---|---|---|
| `last_mode` | `u8` | `2` (Director) | 0..5 (`ModeId`) | Runtime mode to resume at boot |
| `ir_en` | `bool` | `true` | - | IR transmitter enabled |
| `scr_puls_en` | `bool` | `true` | - | Local LCD pulse animation enabled |
| `mst_chan` | `u8` | `1` | {1, 6, 11} | Director Wi-Fi channel |
| `mst_src_id` | `u8` | random in `0x00-0x3F` at first boot | `0x00-0x3F` | Director source_id for channel 1 (community range). Stable per device; reused across reboots. See [section 3.4](#34-source-identifier-partitioning). |
| `slv_chan` | `u8` | `0` (auto) | {0, 1, 6, 11} | Lume Wi-Fi channel; 0 = auto-scan |
| `slv_repeat` | `bool` | `false` | - | Lume operates as repeater |
| `slv_group` | `u8` | `0` (broadcast) | 0..255 | Lume receive-filter group |
| `active_show` | `string` | `"simple-beat"` | up to 16 bytes | Currently selected Show plug-in id |

### B.3 Per-plug-in namespaces

Show plug-ins, output bindings, and visualisations (legacy) each receive a sub-namespace for their own properties. The convention is:

- `ns_<id>` for Show plug-ins (e.g. `ns_dynamic` for the Dynamic show's `groups` property).
- `nb_<id>` for OutputBinding plug-ins.
- `nv_<id>` for Visualisation plug-ins (legacy; deprecated as of Epic 4.7).

The plug-in id MUST be twelve bytes or fewer; longer ids are truncated.

### B.4 Migrations

The reference firmware applies one-shot migrations on first boot after a firmware upgrade. As of v0.6 the migrations are:

- Drop the legacy `slv_ir_grp` key (the function moved to the Lume's `slv_group` filter and per-binding namespaces).
- Map the legacy `active_vis` value `"beat-pulse"` or `"spectrum-bars"` to `active_show = "simple-beat"`.

Migrations MUST be idempotent.

---

## Annex C: Reference test vectors

This annex provides canonical byte sequences for parity testing against the reference firmware. Implementers building a NocturNation transmitter or receiver SHOULD verify against these vectors before deploying.

### C.1 ESP-NOW `LIGHT_PULSE` frame

A `LIGHT_PULSE` from source_id 1, sequence 42, broadcast (`target_class = 0x00`, `target_group = 0x00`), red `(255, 0, 0)`, envelope (attack=`T_96_MS`, sustain=`T_0_MS`, release=`T_480_MS`), chance `CHANCE_100`:

```
Offset  Byte    Field
0x00    0x4E    magic[0] ('N')
0x01    0x4E    magic[1] ('N')
0x02    0x02    protocol_version
0x03    0x01    source_id
0x04    0x2A    sequence_number (42)
0x05    0x00    hop_count
0x06    0x03    message_type (LIGHT_PULSE)
0x07    0x09    payload_len
0x08    0x00    target_class (All)
0x09    0x00    target_group (broadcast)
0x0A    0xFF    r
0x0B    0x00    g
0x0C    0x00    b
0x0D    0x02    attack (T_96_MS)
0x0E    0x00    sustain (T_0_MS)
0x0F    0x04    release (T_480_MS)
0x10    0x00    chance (CHANCE_100)
```

Total frame length: seventeen bytes (eight header + nine payload).

### C.2 ESP-NOW `HEARTBEAT` frame

A `HEARTBEAT` from source_id `0x21`, sequence `0x07`, hop_count 2, carrying tick `0x12345678`, days-since-2026 `0x0123`, centiseconds-today `0xABCDEF`. This matches the byte-for-byte test vector in `test/test_espnow_frame/test_main.cpp::test_heartbeat_wire_format_byte_for_byte`:

```
Offset  Byte    Field
0x00    0x4E    magic[0] ('N')
0x01    0x4E    magic[1] ('N')
0x02    0x02    protocol_version
0x03    0x21    source_id
0x04    0x07    sequence_number (7)
0x05    0x02    hop_count
0x06    0x00    message_type (HEARTBEAT)
0x07    0x09    payload_len (9)
0x08    0x78    tick LE byte 0
0x09    0x56    tick LE byte 1
0x0A    0x34    tick LE byte 2
0x0B    0x12    tick LE byte 3
0x0C    0x23    days_since_2026 LE byte 0
0x0D    0x01    days_since_2026 LE byte 1
0x0E    0xEF    centiseconds_today LE byte 0
0x0F    0xCD    centiseconds_today LE byte 1
0x10    0xAB    centiseconds_today LE byte 2
```

Total frame length: seventeen bytes (eight header + nine payload).

### C.3 PixMob infra-red frame

Single-colour red `(255, 0, 0)` to group 0 (broadcast), envelope (attack=`T_96_MS`, sustain=`T_0_MS`, release=`T_480_MS`), chance `CHANCE_100`:

Pre-encoding byte sequence (before `ENCODING_MAP` lookup):

```
Offset  Byte    Field
0x00    0x80    sync_byte
0x01    [chk]   checksum (computed; see A.2)
0x02    0x00    type_and_on_start (single colour, on_start=0)
0x03    0x00    g_val_6bit (0 >> 2)
0x04    0x3F    r_val_6bit (255 >> 2 = 63)
0x05    0x00    b_val_6bit (0 >> 2)
0x06    0x10    attack_and_chance (attack=2 << 3 | chance=0 = 0x10)
0x07    0x20    release_and_sustain (release=4 << 3 | sustain=0 = 0x20)
0x08    0x00    restrict_group_id (broadcast)
```

The checksum at offset 1 is the sum `0x00 + 0x00 + 0x3F + 0x00 + 0x10 + 0x20 + 0x00 = 0x6F`, then `(0x6F >> 2) & 0x3F = 0x1B`, then `ENCODING_MAP[0x1B]` (consult upstream `jamesw343/PixMob_IR` for the encoding map).

### C.4 Authoritative source

These hand-derived vectors are illustrative. The authoritative reference vectors are generated by running `python3 tools/pixmob_reference_encoder.py` (against the upstream Python encoder) and recorded in `test/test_pixmob_parity/`. Implementers MUST verify against the in-tree test fixtures rather than the inline vectors above.

---

## Annex D: Protocol version history

| Version | Date | Spec doc | Notable changes |
|---:|---|---|---|
| 0x01 | 2026 | (superseded) | Initial public protocol. ESP-NOW transport, 6-byte header, two active message types (`HEARTBEAT`, `LIGHT_PULSE`) plus `EXTENSION` reserved, class-and-group addressing, PixMob IR annex. |
| 0x02 | 2026 | This document | Added 2-byte magic prefix (`0x4E 0x4E`, ASCII "NN") at frame offset 0..1 to discriminate NocturNation traffic from other ESP-NOW users sharing the channel at event-density deployments. Header grew from 6 to 8 bytes; all other offsets shift +2. Wire-incompatible with v1: v1 and v2 receivers cannot interoperate. |

Future revisions will be appended to this table. Conventions layered on top of an existing protocol version (without a wire-format change) are not tracked here; they are documented inline in the relevant section. Non-versioned additions on top of v0x02 include:

- **Source_id partitioning rules** (2026-05-17, [section 3.4](#34-source-identifier-partitioning)) — layered convention on the existing 1-byte field, no wire change.
- **Display message types** `0x09..0x0C` (Epic 13, 2026-06-14, [section 3.3.6](#336-text_display-0x09) onwards) — new codepoints, backward-compatible under the v0x02 forward-compatibility rule (old v0x02 receivers see them as unknown types and silently drop per [section 3.2](#32-message-types)).
- **Long Range PHY mode** (Epic 15, 2026-06-27, [section 2.1.1](#211-phy-mode-long-range-lr)) — PHY-layer configuration, not a wire-format change per se, but a fleet-wide invariant a mixed-mode Director/Lume pair would not appear to interoperate.

---

## References

Bradner, S. (1997) *Key words for use in RFCs to indicate requirement levels*. RFC 2119, IETF. Available at: <https://www.rfc-editor.org/rfc/rfc2119> (Accessed: 12 May 2026).

Espressif Systems (no date) *ESP-NOW protocol reference*. Available at: <https://docs.espressif.com/projects/esp-idf/en/latest/esp32/api-reference/network/esp_now.html> (Accessed: 12 May 2026).

Wilson, J. (no date) *PixMob_IR: reverse-engineered PixMob infra-red encoder*. GitHub. Available at: <https://github.com/jamesw343/PixMob_IR> (Accessed: 12 May 2026).
