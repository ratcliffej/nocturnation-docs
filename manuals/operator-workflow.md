---
title: "NocturNation operator workflow"
status: Draft
notion_url: https://www.notion.so/365bd067740581bbace6c5ac7b2c0339
notion_id: 365bd067740581bbace6c5ac7b2c0339
last_synced: 2026-08-19
sync_direction: bidirectional
---

# NocturNation operator workflow

A short, practical guide for operators running a NocturNation deployment. Covers channel selection, Performance Mode (channel 11) operations, source_id verification, and what to do when things go wrong on the night. For the wire-level spec read the [protocol manual](protocol-manual.md). For the audience-facing badge UI read the [user manual](user-manual.md).

## Channels at a glance

NocturNation uses three of the standard non-overlapping 2.4 GHz Wi-Fi channels. Pick one per deployment; the Director is fixed to that channel.

| Channel | Use | source_id allocation | Lume access control |
|---:|---|---|---|
| 1 | Hobby / community / hackspace | Stable per device (community range `0x00-0x3F`, persisted to NVS) | Permissive: Lumes accept any source_id |
| 6 | Advanced operator override | Operator-discretionary; Director SHOULD pick a Performance-range id | Permissive: Lumes accept any source_id |
| 11 | Performance mode (curated shows) | Random per boot (Performance range `0x40-0xFE`, listen-before-broadcast) | Strict: Lumes only accept Performance-range source_ids |

If you're running a hackspace gig, a personal demo, or a recurring community event where the same Director comes back repeatedly, **use channel 1**. The stable source_id means a returning audience Lume recognises the same Director across power-cycles.

If you're running a curated show at a venue like EMF where attendees with badges might be tinkering on their own M5 Sticks, **use channel 11**. The Performance Mode protections (random per-boot source_id + listen-before-broadcast on the Director, cross-range filter + TOFU on the Lume) keep the audience locked to your show.

Channel 6 is reserved for advanced operators who need a third channel - e.g. running two simultaneous deployments in the same venue, or working around interference on 1 or 11. It carries no automatic protection; configure it as you would channel 1, but operators SHOULD pick a Performance-range source_id by convention.

## Performance Mode (channel 11)

### How the Director allocates its source_id

On every boot, the Director picks a fresh random source_id in the Performance range (`0x40-0xFE`, 191 slots) and listens for ~1 second on channel 11 before transmitting. If another Director is already broadcasting on that same source_id, the Director re-rolls and listens again. After three re-rolls with collisions on every attempt it proceeds with the last pick and logs a warning. The probability of three consecutive collisions with three concurrent Directors is well under one percent; in practice every show starts with a unique id.

The chosen id is shown on the Director's M5 Stick screen as `P:nn` (for example `P:4F`) in the bottom-right corner. This is the value the audience will lock to.

### How audience Lumes lock to your Director

When a Tildagon (or future Lume) powers on or rescans, it listens on its configured channel for the first valid frame from a non-broadcast source_id. The first such frame establishes a Trust-On-First-Use (TOFU) lock; subsequent frames from any other source_id are silently dropped for the rest of the session. On channel 11 specifically, only Performance-range source_ids are eligible to be locked - a misconfigured Director announcing a community-range id on channel 11 will be ignored entirely.

The locked id is shown on the Lume's screen as `ch 11 P:4F` (or `ch 11 C:nn` on channel 1). Audience members can verify they're locked to *your* Director by comparing the value on their badge to the value on your Director's screen.

The lock expires after ten seconds of no frames from the locked source. After that, the Lume re-enters listen state (`ch 11 listen`) and will accept the next valid frame as a fresh lock.

## Pre-show checklist

1. **Power the Director first.** Boot it before the audience arrives so it claims its source_id before any badge tries to lock to it. Lumes that arrive first risk locking to a tinkerer's M5 Stick if one happens to be broadcasting on channel 11.
2. **Note the source_id on the Director screen** (`P:4F`, etc.). Keep it visible during the show - operators and audience members can use it to verify locks.
3. **Pre-flight the renderers.** Switch the Director into Test Mode, fire a Rainbow or Sparkle pulse, and confirm a couple of nearby Tildagons light up and their screens show `ch 11 P:4F` matching your Director.
4. **Switch the Director back to Director Mode** before the audience arrives. The Test Mode menu owns the screen until you exit; the Show plug-in takes over once you're back in Director Mode.

## During the show: spot-checks

If something looks off - a section of the audience not lighting up, a single badge stuck dark - walk up and look at the badge's screen. Three states are informative:

- **`ch 11 P:4F`** matching your Director's id: locked correctly to your show. If the LEDs aren't firing, the issue is downstream (group filter, IR alignment for bracelets - see the user manual). Calm mode does not apply to Lumes as of Epic 19; it's a Director-side toggle the LD reaches for.
- **`ch 11 P:nn`** showing a *different* id than yours: locked to another Director. Most likely a tinkerer on the same channel; ask the operator nicely to switch to channel 1 or stop broadcasting. Alternatively, ask the badge owner to open the settings menu and select "Rescan"; the next valid frame from your Director will establish a fresh lock.
- **`ch 11 listen`** or **`ch 11 scan`**: not currently locked to anyone. Either the badge just powered on (give it a few seconds) or its TOFU lock has expired due to a frame gap. The next valid frame will re-lock it.

## Competing Director on the same channel

You can't fully prevent another operator from booting a Director on the same channel mid-show. The Performance Mode protections defend Lumes against *accidental* disruption (the tinkerer's badge picks a Performance-range id randomly and your Lumes are already locked to your id) but cannot stop a determined attacker reading the open-source firmware and crafting a colliding frame stream.

If you spot a competing Director:

1. **Diagnose by ID**: look at the source_id on the affected badge versus your Director. Mismatched id = competing transmitter; matching id but no LEDs = downstream issue (group filter, IR alignment).
2. **Operational coordination**: ask the tinkerer to stop broadcasting. Most accidental cases will gracefully comply; that's the design assumption.
3. **Rescan**: operator can rescan their badge through the settings menu to break the lock and pick up your stream if it's louder / closer.

There's no cryptographic protection at this protocol version (Tier 0); see the [security RFC](https://www.notion.so/358bd0677405817b8a60de0834511ce5) for the deferred Tier 1+ plans.

## Honest residual risk

A badge that powers on *after* a tinkerer's M5 Stick but *before* your Director's first frame will lock to the tinkerer. The boot-Director-first checklist above is the operational mitigation. There is no technical defence at protocol version `0x02`.

The rescan flow (`Settings → Rescan`) clears the lock and lets the badge re-acquire from the loudest stream nearby. This is the operator's escape hatch when the wrong-lock case occurs in practice.

## Channel 1 (community / hobby)

Channel 1 deployments don't carry these access-control mechanics. The Director allocates a stable community-range id at first boot and reuses it across reboots. Lumes accept any non-broadcast id and TOFU-lock to the first frame they see.

The trade-off is by design: channel 1 prioritises ease of use and "any community member with a NocturNation Director can light up nearby badges" over collision resistance. If two community Directors operate in the same room, the audience badges lock to whichever they hear first - that's not a bug, that's the social contract on channel 1.

## Group addressing conventions

The wire's `target_group` field addresses which subset of a device class receives a frame. The protocol reserves `target_group = 0` as "all devices of the addressed class"; values 1..65534 are specific groups. The wire allows the full range, but the current UI (`Config > Group`) exposes only 0..15 - enough for any realistic deployment.

Within that operator-visible range, NocturNation applies the following **deployment convention**. It is not enforced by any code path; it is a shared convention between show authors, cue authors, and operators so a show emitting `target_group = 3` gets the render its author intended.

| `target_group` | Assigned to |
|---:|---|
| `0` | Broadcast within the addressed class (fires on every device that accepts the class). |
| `1..9` | **Capability-rich devices.** Tildagons, StickC-driven LED strips, AtomS3R-driven LED strips, any Lume that supports the full LIGHT_WASH family (drift + cycle + intensity), fast frame rates, and the full 24-bit colour range. |
| `10..12` | **PixMob legacy fleet.** Adopted 2026-07-27. Segregated from the main-group range so shows can drive rich content into 1..9 without being held back by PixMob limitations (no wash cycles, restricted colour palette, IR-imposed ~50 ms inter-frame gap, hardware clamp to `target_group ∈ 1..31`). |
| `13..15` | Unassigned; reserved for future device families. |

**When authoring a show or a cue**, target 1..9 for the main visual layer and add explicit 10..12 shots when you want PixMobs to fire. A show that only targets 1..9 is silent on PixMobs by design.

**When adding a new device to the fleet**, assign it a group in 1..9 unless it has PixMob-class limitations. The Config menu's `Group` cycle wraps 0..15; pick a value and stick with it.

**Group 0 broadcasts** across the whole class, which under this convention now sprays both the main-group renderers *and* the PixMob-bank. That is usually the intent for opening cues ("everything alive, on my mark") but is worth calling out — a `target_group = 0` LIGHT_WASH will fire the PixMobs' broadcast wash treatment as well as the strips' cycled version.

**Protocol side**, see the [protocol manual §4.2](protocol-manual.md#42-group-filtering) for the wire-level semantics of `target_class` + `target_group` and the PixMob receive-side hardware clamp.

## Running NocturNation on a Tildagon badge (Epic 6B)

The EMF Tildagon badge runs the NocturNation app as either a **Lume** (audience receiver) or a **Director** (IMU tap-to-beat, broadcasting to nearby badges on the hobby channel). It launches into an **idle start menu** - Lume Mode / Director Mode / Settings / Help / Quit - and only starts using the radio once you pick a mode.

### WiFi must be off while a mode runs

This is the one thing to know. The badge's WiFi and ESP-NOW share a single radio, and an *idle, unassociated* WiFi connection makes the ESP32 firmware sweep across channels hunting for an access point - which stomps on ESP-NOW reception (frames drop, the badge can't hold a channel). So while a Lume or Director session is **actively running**, the app takes the radio with `wifi.stop()` and the badge has **no WiFi / app-store / internet**. This is normal and expected; the lights are the cue that a session is live.

- In the **idle menu** (before you start a mode), WiFi is up - connect the badge, browse the app store, etc.
- **Starting** Lume or Director drops WiFi.
- **Back (F)** stops the mode and **restores WiFi**, returning to the idle menu. Switching to another badge app keeps the session running in the background (WiFi stays off, lights stay live).
- **Quit** restores WiFi, hands the LEDs back to the badge, and exits to the launcher.

### Channel

The Tildagon Director **transmits on channel 1 only** (Epic 5.5 reserves the channel-11 Performance band for M5 Directors). For a Tildagon **Lume**, pin the channel to match the Director: **Settings → Channel → `1`** (or `11`). Leaving it on `auto` runs a 11→1→6 scan that can mis-lock onto a neighbouring channel in a busy RF environment - pin it for a reliable show. (A change applies on the next launch.)

### Help screen - QR code

**Help** (idle menu) shows a QR code linking to the project site (`http://www.nocturnation.net` by default; configurable via `help_url` in the badge's `/nocturnation_settings.json`). Hand it to a curious attendee to point them at the project.

### Director button map

| Button | Action |
|---|---|
| Tap the badge (IMU) | beat - the primary input |
| C (CONFIRM) | manual tap (button fallback if the IMU isn't tuned) |
| B (RIGHT) / E (LEFT) | cycle the Show's controls (e.g. palette) |
| A (UP) | Show picker |
| D (DOWN) | per-Show settings (incl. tap sensitivity) |
| F (CANCEL) | stop → idle menu |

If taps feel unresponsive, raise the sensitivity (D → Settings → Sensitivity → High) - a hand-held badge tap is gentle.

## BLE pairing (Epic 20)

Every Director and Lume in the fleet exposes a NocturNation Bluetooth service the operator can use to configure the device without editing the on-disk settings file or reflashing. Full byte-level spec is in the [BLE service manual](ble-service.md); this section covers the operator flow.

Physical presence is the access control mechanism. There are no cryptographic keys — the pairing window only opens when the operator triggers it on the device, and the device drops off Bluetooth entirely once the window closes. Config writes are only accepted while the window is open; reads (device info, current settings) are always accepted so the app can display the current state.

### Which host, which gesture

| Host | Gesture | Feedback |
|---|---|---|
| M5Stack StickC Plus2 (Director or Lume) | Config menu → **BLE Pair** (top-level entry) | LCD shows countdown + advertising name + "Paired!" / "Timeout" flash on close. |
| M5Stack Atom Lite (Lume) | Hold the front button for ~3 s | Pixel 0 slow-pulses blue during the window; whole strip flashes green on success, red on timeout. |
| EMF Tildagon (Lume) | Settings menu → **BLE Pair** | LCD shows countdown + advertising name; "Paired!" text on success. |
| M5Stack AtomS3 Lite / AtomS3 PoE (Director) | Deferred to a follow-on epic; use the Config menu on a StickC in the meantime. | — |

### Bench-testable now (any BLE client)

Until the NocturNation phone app ships, nRF Connect (iOS/Android) or `bleak` on a laptop is enough to drive the whole flow. Rough script:

1. Trigger the pairing gesture on the target device. Confirm the advertising name that appears on the device or in the scan list — format is `NTN<5 hex chars>` (e.g. `NTN3F7A2`, derived from the device's Bluetooth MAC) unless a `friendly_name` has been set, in which case that name appears verbatim. Role (Director / Lume) is deliberately not in the name — an Atom acting as a Director is a planned future variant, and roles are surfaced via `device_info.role` rather than baked into a static advertising label.
2. Connect. The device exposes a NocturNation service; drill into the characteristics.
3. **Read `device_info`** — a 24-byte structure with role, host, firmware version, and the device's Bluetooth MAC (the fleet's stable device identity). See [ble-service §3.1](ble-service.md#31-device_info-read-only).
4. **Read `config`** — a property-bag payload with the current settings. See [ble-service §3.2](ble-service.md#32-config-read--gated-write) for the format.
5. **Write `config`** with a property-bag containing the keys you want to change. Common ones: `group` (u8), `friendly_name` (utf8), `led_power` (u8, 0..100). For Atom Lite specifically, `strip_chain` (u16) and `strip_group_size` (u8) let you change the strip topology from your phone without a reflash. Full key list in [ble-service §5](ble-service.md#5-well-known-keys-v0x01).
6. **Write `pairing_control`** with the byte `0x01` (`commit`) — the device closes the window, persists everything to NVS, and shows the success flash.

Cancel any pairing session by pressing the device's cancel gesture (B-hold on Stick, F on Tildagon, physical button on Atom Lite) — the device tears BLE down and returns to its normal operating state.

### Bulk pairing

For a batch of devices (a puppet parade, a costume run) that all need the same settings, the pattern is:

1. Before starting the batch, raise `pair_win_s` (Bluetooth-writable) on each device to several minutes rather than the default 30 s.
2. Trigger the gesture on every device you want to configure. They'll all show up in the phone's scan list under their respective advertising names.
3. Walk through the list one at a time from the phone, writing the same property-bag payload to each.
4. Use `pairing_control` value `0x02` (`commit_and_sleep`) instead of `commit` on each — Atom Lite drops to a low-power state and stops advertising, so the phone's scan list gets shorter as you work through the batch. Wake with the front button when you're ready to deploy.

### Coexistence with a running show

Bluetooth and ESP-NOW share the same 2.4 GHz radio on the ESP32, so the current firmware runs them strictly separately: Bluetooth is only reachable when the device is out of Lume Mode / Director Mode (Stick), out of Lume Mode (Tildagon), or between shows (Atom Lite pauses receive during the window). Pair the fleet before the show; wear the fleet during it.

### Losing the pairing register

The device itself remembers its persisted settings across power cycles. The **register of "I've paired to these devices"** lives on whichever phone / laptop did the pairing — if that phone gets lost, the register goes with it. The devices are still configured correctly, they just no longer show up in that particular app's fleet view. Re-pair with a new phone to rebuild the view. A future extension will let a Director act as a fleet-master register (documented in [ble-service §11.3](ble-service.md#113-fleet-master-register-on-director)).

## Reconfiguring a deployed Lume (Epic 21)

Once a Lume has been paired once — captured into the Director's register with its UID + secret — the Director can reconfigure it over ESP-NOW without re-pairing. No BLE round-trip, no physical access to the device. Useful for sewn-in wearables, puppets, or anything embedded past the point of easy reach, and for last-minute re-grouping during soundcheck.

### Capture paths

A Director can capture a Lume into its register in three ways:

1. **BLE Live-scan** (Stick Director `Menu → Config Lumes → Live-scan`): the StickC scans for Lumes advertising `NTN-*`, connects, reads `device_info` + `device_secret`, writes any requested config, and stores `{uid, secret, friendly_name, …}` into its pair register. Requires the target to be in BLE pairing mode (Stick: hold BtnA for 5s; Atom: hold Btn1 for 2s). This is the primary path for BLE-capable Lumes.
2. **ESP-NOW capture burst** (Stick Director `Menu → Config Lumes → Capture via ESP-NOW`): the Director listens on the show channel for `UID_ANNOUNCE` frames. The target Lume emits a 10-second burst on an operator-triggered gesture — on Atom Lite that gesture is **Btn1 DoubleTap-then-Hold** (click, release, click, hold until the LED strip pulses white). The Director confirms "capture X?" and stores the tuple. This is the only path for pure-receive hardware without BLE silicon.
3. **Pre-populated register** (future): bulk import from a shipping manifest or a QR-code sheet. Not shipped yet.

### Paired-fleet route

Once Lumes are in the register, select `Menu → Config Lumes → Paired` on a StickC Director. The list shows friendly names and host icons. Pick a Lume, edit its properties (group, host-specific keys), and the Director sends a signed `CONFIG_WRITE` over ESP-NOW. The Lume applies the change, flashes pixel 0 white for ~500 ms, and sends a `CONFIG_ACK` back if the return path is clear.

**Three outcomes to recognise:**

- **Green "Applied"** — ack received, matches the write's numonce, status 0. You can move on.
- **Amber "Written, no confirmation"** — the write went out, no ack came back inside the timeout (default 500 ms). This is **not a failure**. In a crowded RF environment, or on a repeater-fed Lume whose return path is asymmetric, the ack frame can drop while the write landed cleanly. The visible pixel-0 white flash on the device itself is independent evidence; if you can see the device, that flash tells you the write landed. Retry if you can't see the device and the setting matters.
- **Red "Rejected"** — the Director got an ack with a non-zero status. The write was received and authenticated but at least some keys weren't applied (bag decode error, unknown key, out-of-range value). Check the key against the host's property schema.

**Fresh pair → first write.** On a brand new capture, the Director's numonce counter is already monotonically ahead of anything the Lume has seen (the Lume starts with its 16-slot LRU empty, so any valid numonce passes), so the first CONFIG_WRITE after capture Just Works. There is no "sync" step.

**Lost register, Lume still in field.** If the Director's register is wiped (phone lost, Stick re-flashed with cleared NVS), you have no way to reconfigure a deployed Lume except by re-capturing it via one of the three paths above. The Lume itself is unchanged and continues to render whatever its last applied config said. Plan for this — the register is not disposable.

**Visible confirmation always.** Even without an ack frame, every successful `CONFIG_WRITE` apply flashes pixel 0 white for ~500 ms. On a visible Lume this is the operator's load-bearing feedback signal: if the device flashed, the write landed and was authenticated against the right secret. A device that doesn't flash either didn't receive the frame or isn't the device you think it is.

### Security notes

The CONFIG_WRITE path is authenticated with HMAC-SHA256 using the Lume's 16-byte secret. An attacker without the secret can broadcast arbitrary frames at the Lume and nothing changes — the Lume silently drops any frame whose HMAC doesn't verify. The secret only crosses the air during an operator-triggered pairing burst (UID_ANNOUNCE) or during an active BLE pairing session, so passive RF capture during a show does not leak it.

The `UID_ANNOUNCE` burst does broadcast the secret in the clear during the 10-second window. In a hostile RF environment, run captures in a side room or at low power. The operator-triggered gesture is the gate; don't leave a device announcing in a crowded space.

See [protocol-manual §8](protocol-manual.md#8-authenticated-config-channel) for the normative wire specification.

## Where to learn more

- [Protocol manual §3.4](protocol-manual.md#34-source-identifier-partitioning) - normative spec for the source_id partition + TOFU rules.
- [Protocol manual §5](protocol-manual.md#5-channel-discovery) - channel selection and scan rules.
- [BLE service manual](ble-service.md) - byte-level normative spec for the pairing service, well-known keys, and future extensions.
- [Flow diagrams §9](flow-diagrams.md#9-channel-discovery-and-re-scan) - Mermaid renderings of the channel discovery and re-scan state machines.
- [User manual](user-manual.md) - audience-facing badge UI.
