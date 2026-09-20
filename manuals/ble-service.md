---
title: "NocturNation BLE service manual"
status: Draft
ble_service_version: 0x01
firmware_version: "v0.5"
notion_url: TBD
notion_id: TBD
last_synced: TBD
sync_direction: bidirectional
---

# NocturNation BLE service manual

> Normative specification of the NocturNation Bluetooth Low-Energy GATT service: identity, characteristics, property-bag payload format, pairing lifecycle, coexistence with ESP-NOW, and forward-compatibility rules. Companion to the [protocol manual](protocol-manual.md), which covers ESP-NOW; this document covers BLE only.

The BLE service is the durable **control channel** for a NocturNation fleet — it lets an operator (via a phone app, a laptop CLI, or a manual client like nRF Connect) configure a device's identity and settings. The **show channel** stays on ESP-NOW; BLE writes settings, ESP-NOW carries frames. The two channels share the property-bag data model defined in [§4](#4-property-bag-tlv-format), so future extensions (notably [MAC-addressed ESP-NOW config](#11-future-extensions), a documented follow-on to Epic 20) can reuse the same key namespace.

## 1. Roles and hosts

The service is implemented on both **Director-role** and **Lume-role** devices. Every host with BLE-capable silicon runs the same GATT service; the `device_info` characteristic (§3.1) discriminates.

| Host | Role(s) supported | BLE stack |
|---|---|---|
| M5Stack StickC Plus2 | Director or Lume | NimBLE (Arduino ESP32 core) |
| M5Stack Atom Lite | Lume | NimBLE (Arduino ESP32 core) |
| M5Stack AtomS3 Lite | Director | NimBLE (Arduino ESP32 core) |
| M5Stack AtomS3 PoE | Director (with ArtNet-in) | NimBLE (Arduino ESP32 core) |
| EMF Tildagon | Lume | MicroPython BLE (`aioble` or badge SDK) |

Non-BLE devices (currently: none in the fleet) are silent on this channel; they can still be controlled via the on-device Config menu.

## 2. Service identity

**Service UUID**: `2086dee9-a671-44b1-ae81-123829bd4c88`

128-bit random UUID, self-assigned (no Bluetooth SIG registration required). Advertised whenever the device is in the **pairing window** (§7); not advertised otherwise, so the device is invisible to BLE scanners during normal operation.

**Advertising name**: `NTN<5 hex chars>` — for example `NTN3F7A2`. 8-char total, `NTN` prefix + 20 bits from `bt_mac[3..5]` (`bt_mac[3]` full byte + `bt_mac[4]` full byte + `bt_mac[5]` high nibble). Role deliberately isn't in the name — an Atom acting as a Director (driven by a phone app or USB serial) is a planned future variant, so the name shouldn't hard-code a role that might change. Clients that need the role read `device_info.role` (§3.1). The 20-bit suffix gives ~1M collision-free identifiers, comfortably enough for the small fleets NocturNation targets; the full 6-byte BT MAC in `device_info.bt_mac` remains the authoritative identity for anything that cares.

Kept short deliberately: primary BLE ADV packets cap at 31 bytes, and even with the 128-bit service UUID moved to the scan-response (see below) a name over ~26 chars would leave no room for future ADV additions. `NTNXXXXX` also fits comfortably on the 240-pixel StickC LCD at size-2 text.

The service UUID is carried in the **scan response**, not the primary advertising packet, so scanners' `isAdvertisingService()` filter still catches NocturNation devices while the primary ADV keeps its full name budget.

**Override**: an operator-set `friendly_name` (§5 — writeable via the `config` characteristic) replaces the fallback advertising name. Recommended for permanently-installed devices (`Front Left Puppet`, `Stage Left Rail`, etc.).

## 3. Characteristics

| # | Characteristic | UUID | Access | Payload |
|---|---|---|---|---|
| 3.1 | `device_info` | `37b33d23-b02d-4705-be47-b19c57a981ee` | Read | Fixed 24-byte structure |
| 3.2 | `config` | `381b3227-28f7-4036-9ff0-cb76a6e43430` | Read / Write (gated) | Property-bag TLV |
| 3.3 | `status` | `52a398a9-2c10-4291-885e-2a98a8f4d54e` | Read + Notify | Fixed 12-byte structure |
| 3.4 | `pairing_control` | `72678cdb-1b65-417c-8744-751eff153bf0` | Write | 1 byte enum |
| 3.5 | `show_passthrough` | `17cc926e-f45e-41d8-ab61-1cb71d744c35` | Write (refused in v0x01) | Property-bag TLV — reserved |
| 3.6 | `diagnostics` | `2b53e72d-0e3d-4083-9f76-d5d472a26356` | Read + Notify — reserved | TBD |

### 3.1 `device_info` (read-only)

Static apart from `uptime_s`. Format (little-endian, packed):

| Offset | Size | Field | Semantics |
|---:|---:|---|---|
| 0 | 1 | `service_version` | `0x01` for this specification. |
| 1 | 1 | `role` | `0x01 = Director`, `0x02 = Lume`. |
| 2 | 1 | `host` | `0x01 = StickCPlus2`, `0x02 = StickCS3`, `0x03 = AtomLite`, `0x04 = AtomS3Lite`, `0x05 = AtomS3PoE`, `0x06 = Tildagon`. |
| 3 | 1 | `fw_ver_major` | Firmware major version. |
| 4 | 1 | `fw_ver_minor` | Firmware minor version. |
| 5 | 1 | `fw_ver_patch` | Firmware patch version. |
| 6 | 1 | `wire_ver` | ESP-NOW wire protocol version (`0x04` at time of writing). |
| 7 | 1 | reserved | Zero. Ignore on read. |
| 8 | 6 | `bt_mac[6]` | Bluetooth MAC address, big-endian. **This is the fleet UID** (§10). |
| 14 | 4 | `uptime_s` | Seconds since device boot, little-endian u32. |
| 18 | 6 | reserved | Zero. Ignore on read. |

Total: 24 bytes.

Always readable — no permission gate. `device_info` reads leak no operator secrets (all values are physically visible on the device or in the enclosure marking).

### 3.2 `config` (read + gated write)

**Read**: returns the device's full current property bag (§4), serialised as TLV. Every well-known key with a currently-persisted value appears in the bag. Absent keys mean "using default"; the reader can consult §5 for defaults.

**Write**: property-bag TLV. Applied atomically: the device parses the bag, validates every entry, then commits recognised keys to NVS in a single transaction. Unrecognised keys are silently ignored (forward-compat). Writes return a status code:

| Code | Semantics |
|---:|---|
| `0x00` | Success — all recognised entries applied and persisted. |
| `0x81` | Not in pairing window — write refused. |
| `0x82` | Malformed TLV — no keys applied. |
| `0x83` | Value out of range for the key type — no keys applied. |

### 3.3 `status` (read + notify)

Dynamic device state. Read returns current snapshot; subscribed clients receive notifications on significant state changes (rate-limited to 1 Hz maximum). Format (little-endian, packed):

| Offset | Size | Field | Semantics |
|---:|---:|---|---|
| 0 | 1 | `running_mode` | Directors: `0x01 = Director`, `0x02 = Test`, `0x03 = Config`. Lumes: `0x11 = Lume-running`, `0x12 = Lume-idle`, `0x13 = Lume-locked-and-receiving`. |
| 1 | 1 | `lock_state` | Lumes only: `0x00 = unlocked`, `0x01 = TOFU-locked`, `0x02 = bound-sid-locked`. Directors: `0x00`. |
| 2 | 2 | `source_id` | Little-endian. For Directors: the current on-air `source_id`. For Lumes: the currently-locked `source_id` (`0xFFFF` if unlocked). |
| 4 | 1 | `battery_pct` | 0..100 %. `0xFF` = unknown / no battery. |
| 5 | 1 | `signal_pct` | Signal-quality proxy: for Lumes, `0..100` derived from frame arrival rate over the last window. `0xFF` = not applicable. |
| 6 | 4 | `airtime_drops` | Little-endian u32. For Directors: count of ESP-NOW sends refused by the airtime cap. For Lumes: `0`. |
| 10 | 2 | reserved | Zero. Ignore on read. |

Total: 12 bytes.

Always readable. Reads always succeed regardless of pairing-window state.

### 3.4 `pairing_control` (write-only)

Single-byte action:

| Value | Action |
|---:|---|
| `0x01` | `commit` — close the pairing window early; keep device running normally. |
| `0x02` | `commit_and_sleep` — close the pairing window, then put the device into deep sleep. Wake by physical button press (host-dependent gesture). |
| `0x03` | `abort` — close the pairing window without acknowledging any writes. |

Writes are always accepted while a pairing window is open; ignored otherwise.

### 3.5 `show_passthrough` (reserved in v0x01)

Declared for forward-compatibility with future BLE-driven show control (mobile-app M4-shaped features). In v0x01:

- **Reads**: return a fixed sentinel property bag `{"reserved": u8=0x01}` — allows clients to test the feature is declared without triggering an error.
- **Writes**: refused with status code `0x81` (defined as "not implemented in v0x01" in the `show_passthrough` context).

The characteristic UUID is stable; a future service version will populate the write path without changing the UUID. Clients that write and see `0x81` should treat it as a feature-gate signal, not a permanent error.

### 3.6 `diagnostics` (reserved)

Placeholder for a future bench-diagnostic stream (recent-frame counters, drop reasons, receive-path statistics). Not implemented in v0x01; readers should tolerate the characteristic being absent OR present with a zero-length payload.

## 4. Property-bag TLV format

Every payload of the `config` characteristic (and, in the follow-on ESP-NOW extension of §11, the `CONFIG_WRITE` ESP-NOW frame) is a **property bag** — a list of `{key, value_type, value}` entries. Entries are ordered arbitrarily; duplicates are permitted but only the last occurrence of any key is retained.

**Wire format** (all fields packed, no padding):

```
u8   entry_count
repeat entry_count times:
  u8    key_len              // bytes
  u8[]  key                  // ASCII, key_len bytes, NOT null-terminated
  u8    value_type            // see table below
  u8    value_len             // bytes
  u8[]  value                 // value_len bytes; encoding per value_type
```

**Value types**:

| Type ID | Name | `value_len` | Encoding |
|---:|---|---:|---|
| `0x00` | `u8` | `1` | Single byte. |
| `0x01` | `u16` | `2` | Little-endian unsigned 16-bit. |
| `0x02` | `u32` | `4` | Little-endian unsigned 32-bit. |
| `0x03` | `bytes` | `0..255` | Opaque bytes; caller-defined semantics. |
| `0x04` | `utf8` | `0..255` | UTF-8 text; NOT null-terminated. Trailing whitespace is retained. |
| `0x05` | `bool` | `1` | `0x00 = false`, `0x01 = true`. Any other value: implementation-defined. |

**Parser conformance**:

- Malformed entries (key_len or value_len overruns the buffer) abort the parse; the whole bag is rejected with status `0x82`.
- Unrecognised keys are skipped, not rejected — enables forward-compat.
- Value-type mismatch on a recognised key (e.g. `group` written as `utf8`) rejects the whole bag with status `0x83`.

**Size budget**: default BLE MTU is 23 bytes, of which 20 are payload after ATT overhead. A single-entry bag with a 5-char key and a u16 value fits (5 + 1 + 5 + 1 + 1 + 2 = 15 bytes plus 1 for `entry_count`). Larger writes negotiate a bigger MTU (up to 512 bytes) at connect time; the device accepts whatever MTU the phone negotiates.

## 5. Well-known keys (v0x01)

Keys applicable per role:

### 5.1 Lume-role keys

| Key | Type | Range / notes | Default |
|---|---|---|---|
| `group` | `u8` | 0..255. `0` = broadcast-only. | Random `1..3` on first boot. |
| `led_power` | `u8` | 0..100 %. Applied over the host cap (Atom Lite clamps to 10 %). | Host-dependent. |
| `bound_sid` | `u16` | `0xFFFF` = TOFU (existing behaviour); non-broadcast sid = only admit frames from that `source_id` on channels 1/6. | `0xFFFF`. |
| `channel_pref` | `u8` | `0 = auto-scan`, `1`, `6`, `11`. | `0`. |
| `strip_chain` | `u16` | 1..288 pixels. Physical chain length plugged into the Grove port; drives `HAL::LedStrip::set_pixel_count()`. **The only way to configure this on Atom Lite post-flash**, since Atom has no on-device menu. | Per-env build flag `NOCT_DEFAULT_STRIP_CHAIN_SIZE`. |
| `strip_group_size` | `u8` | 1..255 pixels-per-CHANCE-roll. Visual group size within the chain. | Per-env build flag `NOCT_DEFAULT_STRIP_GROUP_SIZE`. |
| `pair_win_s` | `u8` | 5..255 seconds. Pairing-window duration for future gestures. | `30` (build-flag override: `-DBLE_PAIRING_WINDOW_S_DEFAULT=N`). |
| `friendly_name` | `utf8` | 0..20 bytes. Empty string clears. | Empty (falls back to advertising-name convention). |

### 5.2 Director-role keys

| Key | Type | Range / notes | Default |
|---|---|---|---|
| `dir_sid_perf` | `u8` | 0x40..0xFE. Performance-range source_id (channel 11). | Random on first boot. |
| `retx_count` | `u8` | 1..5. §4.3 redundant retransmit count. | `2` (build-flag override: `-DESPNOW_RETRANSMITS_DEFAULT=N`). |
| `pair_win_s` | `u8` | 5..255 seconds. | `30`. |
| `friendly_name` | `utf8` | 0..20 bytes. | Empty. |

**Reserved keys** (not applied in v0x01 but reserved to prevent conflicts):

- `show_intent` (bytes) — future show-command target for the `show_passthrough` characteristic.
- `identify_flash` (u8) — trigger a self-identification flash; may migrate from BLE to ESP-NOW-only in the follow-on extension.

Firmware implementations MUST NOT define keys beginning with an underscore (`_`) — that prefix is reserved for future service-internal use.

## 6. Permission model

The service **does not use BLE bonding or passkey pairing**. There are no cryptographic secrets on the wire. Access control is via **physical presence**:

- The service is only advertised when the device is in a pairing window (§7). Outside the window, the device is invisible to BLE scanners.
- Reads (`device_info`, `config`, `status`) are always accepted during the pairing window. All returned values are already physically visible on the device (labels, screens, LEDs), so reads leak nothing.
- Writes to `config` are gated on the pairing window being active; writes attempted outside return status `0x81`.
- Writes to `pairing_control` are always accepted during the window.
- Writes to `show_passthrough` are refused in v0x01 (§3.5).

**Threat model**: the pairing window is a physical-access gate. An attacker within BLE range at the moment the operator opens a pairing window on a device can enumerate its `config` and issue writes. This is equivalent to being able to reach the device's on-device Config menu — every value writable via BLE is also settable via the physical UI. BLE does not widen the attack surface, it just adds a second entry point to the same knobs.

**Multi-connection**: the device accepts one BLE central at a time. Second connect attempts during an existing session are refused.

## 7. Pairing lifecycle

The device is in one of two BLE states: **Idle** (no advertising, no service, radio disabled) or **PairingActive**. Transitions:

```
[Idle]  --(gesture: button-hold / menu entry)-->  [PairingActive]
[PairingActive]  --(timeout after pair_win_s)-->  [Idle]
[PairingActive]  --(pairing_control::commit)-->  [Idle]
[PairingActive]  --(pairing_control::abort)-->  [Idle]
[PairingActive]  --(pairing_control::commit_and_sleep)-->  [DeepSleep]
[DeepSleep]  --(physical button press)-->  [Idle]
```

The pairing gesture varies by host:

- **StickC / AtomS3 / AtomS3 PoE / Tildagon**: menu item (Config > BLE Pair on Sticks, Settings > BLE Pair on Tildagon). Menu drills into a countdown screen showing the advertising name and remaining `pair_win_s`.
- **Atom Lite**: button hold 3 s. LED enters slow-pulse-blue while in `PairingActive`; short green flash on `commit`; green fade to dark on `commit_and_sleep`.

`pair_win_s` is loaded from NVS at gesture time. Default 30 s; operators expecting a bulk-pairing session (§9) can raise it to minutes via the Config menu or a previous BLE `config` write.

## 8. Coexistence with ESP-NOW

BLE and ESP-NOW share the 2.4 GHz PHY on the ESP32. The v0x01 rule is:

**BLE is only active during the pairing window.** No BLE while a Director is running a show, no BLE while a Lume is receiving frames.

This gives full ESP-NOW airtime during operation, zero coexistence complexity, and defers the harder concurrent-BLE-and-ESP-NOW work to a future service version (which will introduce the writable `show_passthrough` path).

Concretely:

- Director: BLE only reachable while in Config mode. Running the Show plugin returns the device to non-advertising Idle.
- Lume: BLE only reachable while in Settings menu (Tildagon) or during the explicit button-hold pairing window (Atom Lite). Receiving frames while paired is not supported in v0x01.

## 9. Bulk pairing (UX pattern, not protocol)

For deploying a large fleet (~10-50 devices, e.g. a puppet parade), operators set a longer `pair_win_s` on each device beforehand — 5-10 minutes — then walk through the phone/CLI app iterating one device at a time. `pairing_control::commit_and_sleep` drops each paired device into deep sleep so it doesn't clutter subsequent scan lists.

Bulk pairing is not a protocol feature; it's a UX pattern that falls out of the configurable pairing window and the `commit_and_sleep` gesture. Broadcast BLE writes (writing config to N devices in a single air-op) were considered and rejected — the compose-with-per-device-ack pattern above is simpler and doesn't require unusual use of BLE advertising primitives.

## 10. Device identity: Bluetooth MAC as UID

Every ESP32 has a hardware Wi-Fi STA MAC and a Bluetooth MAC deterministically offset by 2 (`BT_MAC = STA_MAC + 2`). **The NocturNation UID is the BT MAC**, exposed via `device_info.bt_mac`.

Rationale: the BT MAC is only air-visible during a pairing window (BLE advertising broadcasts it). The STA MAC leaks continuously in every ESP-NOW frame envelope that a repeating Lume transmits. Using the BT MAC as the fleet UID gives a "the UID is only knowable through a physical pairing event" property, which underpins the future MAC-addressed ESP-NOW config extension (§11).

**Requirement**: firmware MUST disable BLE address randomisation and pin the BT MAC to the hardware value at BLE-stack init. Randomised addresses break the UID stability property.

## 11. Future extensions (not implemented in v0x01)

### 11.1 MAC-addressed config over ESP-NOW

Motivated by devices embedded past the point of easy physical access (puppets, permanent installations, sewn-in wearables). Once an operator has paired to a Lume once over BLE and captured its `bt_mac` into the app's register, they can later reconfigure it without re-pairing, via new ESP-NOW frame types:

- **`CONFIG_WRITE`**: broadcast ESP-NOW frame with payload `{target_bt_mac[6], property_bag_TLV}`. Each Lume compares `target_bt_mac` against its own BT MAC and applies the property bag if match. Uses the same TLV format and key namespace as the `config` characteristic (§4-5). On successful apply, the Lume emits a **short white ack-flash** (~500 ms) as visible confirmation.
- **`IDENTIFY`**: broadcast ESP-NOW frame with payload `{target_bt_mac[6], duration_ms}`. Addressed Lume flashes a distinctive pattern (white pulse at ~2 Hz) for `duration_ms`. Deliberately longer / more spotable than the ack-flash — the operator can find "which puppet is 3F:7A:2B" without opening it up.

Delivery is fire-and-forget (ESP-NOW broadcast has no ACK). The app-side pattern is: emit CONFIG_WRITE → BLE-connect and read `config` afterwards to verify → mark register entry confirmed or retry.

Wire spec implication: two new post-EMF frame types. Deployed hardware silently drops unknown types per [[project-emf-wire-spec-freeze]], so this is compatible with the current wire freeze.

### 11.2 Writable `show_passthrough`

The v0x01 `show_passthrough` characteristic is declared but write-refused. A future service version will define its write path, carrying BLE-encoded LIGHT_PULSE / LIGHT_WASH primitives that the receiving Director translates directly to ESP-NOW frames. This is the substrate for the mobile-app M4 lighting-desk vision.

Enabling `show_passthrough` writes requires the coexistence work deferred by §8 — running BLE and ESP-NOW simultaneously with an acceptable airtime cost. Estimated 10-20 % ESP-NOW airtime reduction while BLE-connected.

### 11.3 Fleet-master register on Director

The v0x01 register lives on the app (phone or laptop CLI). A future extension may put the register on the Director itself, letting multiple apps sync fleet state from a shared source of truth and giving lost-phone recovery a workable path. Design deferred until real operators report the pain point.

## 12. Conformance

A device claiming conformance to BLE service v0x01 MUST:

- Expose the service UUID (§2) via BLE advertising when — and only when — in the pairing window.
- Implement all mandatory characteristics: `device_info`, `config`, `status`, `pairing_control`. `show_passthrough` and `diagnostics` MAY be present or absent; if absent, clients MUST tolerate the missing UUIDs.
- Accept `config` writes only during the pairing window; return the specified status codes.
- Persist all `config` writes to NVS before returning the write ACK.
- Silently ignore unrecognised property-bag keys on `config` writes.
- Reject malformed TLV with status `0x82`.
- Disable BLE address randomisation and use the hardware BT MAC as the advertised address.

A client claiming conformance to BLE service v0x01 MUST:

- Handle service, characteristic, and status codes as defined in §3.
- Tolerate reserved characteristics being absent, and tolerate reserved keys / status codes being unrecognised.
- Not assume any characteristic beyond the four mandatory ones is present.
- Refuse to write to `show_passthrough` in v0x01 conformance mode.

## 13. Test vectors

TBD — populated at implementation time (B3c bench validation) with real byte-level captures of nRF Connect sessions.

## 14. Change history

| Version | Date | Notes |
|---|---|---|
| v0x01 | 2026-09-19 | Initial specification (Epic 20 B2). |
