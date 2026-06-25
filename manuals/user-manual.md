---
title: "NocturNation user manual"
status: Draft
firmware_version: "v0.6"
notion_url: https://www.notion.so/35ebd067740580369c67c6738fe3f6d0
notion_id: 35ebd067740580369c67c6738fe3f6d0
last_synced: 2026-05-23
sync_direction: bidirectional
---

# NocturNation user manual

> A practical guide to running NocturNation at a venue: what it is, how it works, how to set it up, how to configure it, and what to do when it misbehaves.

**Firmware version covered**: v0.6 (`include/firmware_version.h`).
**Reference hardware**: M5StickC Plus2, M5StickS3 (the Sticks); M5Atom Lite; M5Stack SK6812 RGB flex strip (optional); PixMob Aurora bracelets.

The boot flow is the same on every host but defaults to Lume on power-up, so a freshly-flashed device joins an existing fleet immediately. To run a device as a Director, tap any button during the 3-second boot splash to open the mode menu, then pick **Director Mode**. On the Atom Lite, where there is no display and no boot splash, the device boots straight to Lume; the front button doubles as the LED-strip brightness control once a Director is locked (see [section 2.5](#25-m5atom-lite) and [section 2.6](#26-led-strip)).

---

## Quickstart

If you only have five minutes:

1. Flash one Stick as the Director, one or more Sticks as Lumes. Same firmware on every device.
2. Power on the Director. Wait through the boot splash; it lands in **Director** mode by default.
3. Power on each Lume. They auto-scan and find the Director. The status pip on the Lume screen turns solid when traffic is being received.
4. Play music. Wave a Lume in the rough direction of the bracelets. Press Button 1 on the Director to cycle between **Simple Beat** and **Dynamic** shows.
5. If you need to change settings, long-press Button 1 from any mode to enter the configuration tree. Long-press Button 2 from the show screen to pick a different show.

For anything beyond this - including why some bracelets do not respond to a particular show, why the Lume shows NO SIGNAL, or how to address a subset of bracelets - read on.

## Contents

1. [Theory of operation](#1-theory-of-operation)
2. [Hardware](#2-hardware)
3. [Installing the firmware](#3-installing-the-firmware)
4. [Configuration](#4-configuration)
5. [Modes and shows](#5-modes-and-shows)
6. [Troubleshooting](#6-troubleshooting)
7. [Glossary](#7-glossary)
8. [Index](#8-index)

---

## 1. Theory of operation

For a visual reference alongside this chapter, see [flow-diagrams.md](flow-diagrams.md) - Mermaid diagrams covering system topology, the Director analyser pipeline, dispatch fan-out, and the Lume receive flow.

### 1.1 What NocturNation is

NocturNation is a distributed crowd-lighting system. One device (the **Director**) listens to music through its microphone, detects beats and structural events (drops, breakdowns), and decides moment by moment what lights should do. It broadcasts those decisions over a short-range radio link to one or more **Lumes**, which act as range-extending repeaters and infra-red transmitters. Each Lume fires those decisions at the crowd as infra-red light commands, which are picked up by **bracelets** worn by the audience.

The system is deliberately one-way: the Director talks, the Lumes and bracelets listen. There is no audio routing from the front-of-house mixer, no network back to a control surface, and no per-bracelet identity. Everything the system does is driven by what the Director microphone hears, in real time, with under a hundred milliseconds of end-to-end latency.

When a Stick runs as a Lume it becomes part of the show in its own right: its screen and onboard LED light up with the broadcast colour, not only the bracelets it drives. A Lume can be as simple as a bare ESP32 board with a few LEDs wired up, which makes rolling your own wearable straightforward.

The NocturNation protocol is carrier-independent. ESP-NOW is the radio link used today, but the same light commands can run over other carriers - Bluetooth LE, sub-GHz RF, or infra-red (using the NocturNation framing rather than the PixMob one) - without changing what the Lumes do with them.

### 1.2 Why distributed

Three reasons.

**Coverage.** A single Stick's infra-red LED reaches roughly five to fifteen metres of clear line of sight, depending on which Stick and how it is oriented (see [section 2.3](#23-ir-radiation-patterns)). A medium-sized venue needs three or four Lumes spaced around the room to reach every bracelet. The Lumes do not need to hear the music; they only need to be in radio range of the Director and within infra-red line of sight of part of the audience.

**Redundancy.** ESP-NOW, the radio protocol used between Sticks, is a broadcast medium with no acknowledgements. Each light command is transmitted three times in quick succession to absorb the occasional lost frame. A Lume that receives any of the three copies fires the command; a deduplication ring on the receive side ensures it only fires once.

**Operator division of labour.** One operator can run the Director from front of house while helpers stationed around the room hold or stand the Lumes. The Lumes need no configuration during a show; they keep running on whatever channel and group filter you set in advance.

### 1.3 How the Director decides

The Director runs an audio analyser continuously. It samples the microphone at 16 kHz, computes a thirty-two-band logarithmic spectrum every twenty-five milliseconds, and feeds the spectrum into three classifiers:

- A **BeatDetector** that watches the lowest eight bands (roughly 30 Hz to 150 Hz, where kick drums live) for sudden energy spikes. The detector keeps a one-second history of band energy and fires when the current frame exceeds the running mean by 2.2 standard deviations, with a 200 millisecond refractory period to suppress double-fires.
- A **DropDetector** that compares short-window energy (two seconds, "right now") to long-window energy (ten seconds, "the recent past"). When the ratio crosses 1.8 the Director emits a DROP event; when it falls below 0.4 it emits BREAKDOWN. A four-second cooldown prevents the detector from re-firing across sustained passages.
- A set of **music descriptors** that summarise the spectrum each frame: spectral centroid (centre of energy on the frequency axis, a proxy for brightness), broad-band energy, and density (how peaky versus how smeared the spectrum is). These feed into the Dynamic show's colour mapping.

The detectors run on the Director only; Lumes never analyse audio. Tuning history for the detectors lives in [include/dal/analyser/beat_detector.h](../../include/dal/analyser/beat_detector.h) and is captured in the architecture spec.

Not every Director needs a microphone. On the EMF Tildagon badge, which has no mic, the beat source is the **IMU** instead: the operator taps or moves the badge in time with the music and each tap fires a beat that drives the local show and broadcasts to nearby Lumes.

### 1.4 How the bracelets work

PixMob Aurora bracelets are off-the-shelf passive infra-red receivers. They wake on an infra-red command, render the light envelope embedded in the command (attack, sustain, release, with a probabilistic "chance" gate), and then return to standby. They have no on-board state between commands beyond a brief residual envelope; if you fire a new command before the previous envelope finishes, the new envelope replaces the old.

Bracelets ship from the factory pre-assigned to one of thirty-one groups (the group is set at random in the bracelet's electronics), but you can reassign a bracelet's group from a Stick using the PixMob group-set workflow in the configuration menu (see [section 4](#4-configuration)). The infra-red command carries a five-bit group filter byte: a bracelet only responds to a command whose filter byte is zero (broadcast) or matches its own group. This means the operator cannot target a specific bracelet, but can target a subset of the audience: at a venue with bracelets pre-distributed to all groups uniformly, addressing group 1 will light up roughly one bracelet in thirty-one.

The PixMob infra-red encoding is reverse-engineered from upstream work by James Wilson (see [acknowledgements](#acknowledgements)). NocturNation parity-tests every transmitted byte against a Python reference encoder to keep behaviour locked to that upstream.

### 1.5 Bracelet timing and residual state

Bracelets render the envelope encoded in each infra-red command and then drop back to standby, but there is a brief window during the fade-out where they remain receptive. If a new command lands inside that window, the bracelet stitches the two envelopes together; the operator sees colour artefacts in fades or truncated tails on twinkles. This is why high-cadence shows (Rainbow at ~25 ms cycle) look cleaner than sparse ones (Whiteout, Sparkle): consecutive fires keep bracelets in a fresh, fully-overwritten state.

NocturNation manages residue in the **show**, not the dispatch layer: each show picks an envelope duration that fits inside its fire cadence, so the envelope completes naturally before the next fire arrives. Sparkle, for example, runs a 960 ms envelope on a 1100 ms cadence (a ~140 ms safety gap).

An earlier Epic 4.7 build inserted a zero-RGB "reset primer" command in the dispatch layer before every non-trivial fire, on the theory that an extra clear command would scrub residue. Bench testing in Epic 4.8 showed the opposite: the extra IR traffic overloaded the bracelet receivers, and only Rainbow (which already skipped the primer via an idle gate) rendered reliably. The primer was removed; today the dispatch sends exactly one IR command per `render_fx` call.

### 1.6 Class-and-group addressing

Every render command on NocturNation is addressed by a pair of bytes: **target class** and **target group**. The class identifies what kind of device should render the command:

- `0x00` All - every class accepts the command (this is the value used for "broadcast" in everyday operation).
- `0x01` Light - infra-red light bracelets such as PixMob.
- `0x02` Screen - the local LCD on a Stick.
- `0x03` MultiLedScreen - reserved for the Tildagon-class targets that arrive in [Epic 5](https://www.notion.so/358bd067740581b19551d158d658df76).

Values 0x04 to 0xFF are reserved for future device classes (accelerometer sticks, smoke machines, large-format LED panels).

The group is a one-byte filter from `0x00` to `0xFF`. Zero is the **broadcast group**: every device renders a command addressed to group zero, regardless of which group the device is itself configured for. Any other group number addresses only devices whose own group matches exactly. For PixMob bracelets the group range is constrained to 0..31 by the IR protocol; for Lumes on the radio link the range is the full byte. A Lume's group is set by the [Group menu item](#41-top-level) at the top of the configuration tree. A future firmware version will let a Director set the group of every NocturNation Lume in radio range with a single broadcast command, so a whole fleet can be zoned in seconds before a show.

A Lume whose own group is set to zero is in no specific group and only renders broadcasts; it does not act as a receive-side wildcard. Fresh devices are assigned a random group from 1, 2, or 3 at first boot (see [section 4.1](#41-top-level) below) so that a fleet of newly-flashed Sticks naturally distributes across the three drum groups that the Dynamic show routes kick / snare / hi-hat to.

The combined `class:group` form is shown in the developer documentation as a four-hex-digit string (`"00:00"` for global broadcast, `"01:07"` for light-class group 7, `"02:00"` for every screen). Operators rarely interact with the syntax directly; it appears in serial diagnostics and in show authoring.

### 1.7 The wireless link

The Sticks talk to each other over **ESP-NOW**, a connection-less broadcast protocol on the 2.4 GHz band that piggybacks on the 802.11 Wi-Fi physical layer without joining an access point. Each frame is sent as a vendor-specific action frame and is received by every Stick on the same Wi-Fi channel. NocturNation uses three of the standard 2.4 GHz channels:

| Channel | Role | Notes |
|---|---|---|
| 1 | Hobby / open community | Default Director channel; suggested for everyday work and small gatherings |
| 6 | Advanced operator override | Available but rarely used |
| 11 | Show / commercial | Suggested for large public deployments |

The Director picks a channel in [Connectivity > ESP-NOW > Director Channel](#43-connectivity). The Lume defaults to **auto-scan**, in which case it cycles through channels 11 and 1 and locks onto whichever is currently broadcasting (channel 11 is checked first because show traffic takes priority). A Lume can also be locked to a single channel if you want predictability.

Every frame the Director sends is transmitted three times in quick succession on the same channel. Each frame carries a sequence number; Lumes keep a sixteen-deep ring of recently-seen sequence numbers and ignore duplicates. The signal-quality bars on the Lume screen show how many of the recent expected sequences were actually seen. The system never reports raw RSSI; it reports delivered fidelity, which is what an operator actually cares about.

### 1.8 The heartbeat

The Director broadcasts a one-byte **heartbeat** at 1 Hz so that idle Lumes know it is still alive. To keep duty cycle low, the heartbeat is suppressed whenever the Director has sent any other frame in the last second; during a noisy passage with frequent kicks, no explicit heartbeats are needed.

A Lume that goes more than three seconds with no traffic at all displays **NO SIGNAL** and runs no local effect. It does not promote itself to Director; it does not improvise. The Director is the single source of truth, and a missing Director is shown as a clear failure mode rather than masked by a local fallback.

### 1.9 Why bracelets are pre-grouped, not paired

It is tempting to imagine bracelets being paired to specific operators or to seats. They are not. Bracelets are factory-programmed to random groups and remain that way; there is no return path for the bracelet to tell the Director anything about itself. A future companion-app direction could add opt-in seat or group pairing through a phone, but nothing in the system pairs a bracelet to a person or a seat today.

In practice this means the addressing you can do at a venue is **statistical**: addressing group 7 lights up roughly one in thirty-one bracelets, distributed uniformly across the audience. Addressing all groups in sequence creates a "sparkle" pattern. The Dynamic show uses this to route kick drums to group 1, snare hits to group 2, and hi-hat onsets to group 3, producing visibly different reactions from different segments of the crowd to different parts of the song.

A future NocturNation-native bracelet (planned but not in this firmware) will support operator-driven group assignment via a paired return path.

---

## 2. Hardware

### 2.1 Supported Sticks

NocturNation runs on two M5Stack form-factor boards. They share the same firmware and the same configuration tree; the build system selects the right hardware abstraction layer per board.

| Property | M5StickC Plus2 | M5StickS3 |
|---|---|---|
| MCU | ESP32-PICO-V3-02 | ESP32-S3-PICO-1-N8R8 |
| Microphone | PDM (SPM1423) | I2S (ES8311 codec) |
| IR transmitter | GPIO 19 | GPIO 46 |
| IR receiver | None | GPIO 42 |
| Bluetooth | BLE 4.2 | BLE 5.0 |
| On-chip SRAM | 520 KB | 512 KB + 16 KB RTC |
| Embedded PSRAM | 2 MB | 8 MB |
| Embedded Flash | 8 MB | 8 MB |
| Form factor | 24 x 48 x 14 mm | 24 x 48 x 14 mm |
| Status | First-class (legacy hardware) | First-class (current reference) |

Both Sticks have a small LCD, two user buttons (BtnA and BtnB), and a Lithium battery. The S3's power button is owned by its PMIC chip in hardware (short press = soft reset, long press = power off) and is not reachable by firmware; the Plus2's BtnPWR is dropped from the firmware-side mapping for cross-host consistency.

The Plus2 is end-of-life from M5Stack but remains fully supported. Buy the S3 for new deployments; keep the Plus2 in service for as long as the existing ones keep working.

### 2.2 PixMob bracelets

NocturNation targets PixMob Aurora bracelets. These are widely available second-hand from concert merchandise channels, typically in batches of dozens to hundreds. Earlier PixMob generations use the same infra-red encoding and should work but have not been bench-verified by the project; report any compatibility issues against [the repository](https://github.com/ratcliffej/nocturnation-stickc).

Each bracelet has two AAA batteries, seven RGB LEDs behind a diffuser, and an infra-red photodiode on the visible face. They wake on an infra-red command, render the command, and return to deep sleep within a few seconds. Battery life on a fresh set is approximately one large event (eight hours of intermittent activity).

Bracelets ship with a factory group assignment, uniformly distributed across thirty-one groups within a batch. You can re-group them from a Stick using the PixMob group-set workflow (see [section 4.4](#44-utilities)).

### 2.3 IR radiation patterns

The two Sticks have markedly different infra-red radiation patterns and this affects how you deploy them.

- The **Plus2 IR LED** is nearly omnidirectional. It throws a roughly spherical pattern with strong response within five metres in all directions, falling off gracefully out to ten metres. One Plus2 can illuminate a small room from any orientation.
- The **S3 IR LED** is more focused. The strongest response is in a roughly thirty-degree cone in front of the device, with usable range out to fifteen metres or so on-axis. Off-axis falloff is sharp; bracelets behind the S3 receive almost nothing.

In practice:

- **Small venues** (one room, fewer than fifty bracelets): one Plus2 Director placed centrally works on its own.
- **Medium venues** (large room, fifty to two hundred bracelets): one Director plus two to four Lumes, ideally distributed at corners or along long walls. Mix Plus2 and S3 freely; orient the S3s toward the densest crowd areas.
- **Large venues** (multiple rooms, hundreds of bracelets): one Director at front of house, Lumes at every aisle and corner; use the Lume-as-repeater toggle to extend radio range without adding IR sources.

For a per-Stick coverage boost on the Plus2, the firmware also supports an **optional external IR transmitter** wired to the **GPIO 26 header pin** (e.g. the M5Stack IR Transmitter unit). It can run alongside the built-in LED - both emitters fire on every command, back-to-back, roughly doubling coverage - or replace it. Toggles live in `Connectivity > IR` (see [section 4.3](#43-connectivity)). External IR is **Plus2-only**: the S3's header pinout is different and an IR unit plugged in directly drives a strapping pin that forces the chip into boot mode.

### 2.4 Antenna orientation

The Sticks transmit Wi-Fi (and hence ESP-NOW) via a small ceramic patch antenna on the rear of the board. The radiation pattern is broadly hemispherical, biased toward the rear. Two Sticks placed face-to-face on a table will have weaker radio coupling than two Sticks placed back-to-back or both screen-side-up. For maximum radio range, stand Lumes on their butts (USB-C connector down, screen vertical) and orient them so the rear face points roughly toward the Director.

A Lume with the Lume-as-repeater toggle enabled retransmits every accepted frame. Chained repeaters extend radio range significantly at the cost of additional radio latency per hop (around five milliseconds per hop). The default is no repeating; turn it on only when you have measured a coverage gap.

### 2.5 M5Atom Lite

The Atom Lite is a sugar-cube-sized ESP32 board (the same ESP32-PICO family as the Plus2). It has one programmable button, one onboard SK6812 RGB LED, a Grove HY2.0-4P expansion port, and an optional 200 mAh battery base. It has no display, no microphone, no infra-red transmitter, no IMU.

| Property | M5Atom Lite |
|---|---|
| MCU | ESP32-PICO-D4 |
| Display | None |
| Onboard LED | One SK6812 (GPIO 27) |
| Button | One programmable, front face (GPIO 39) |
| Grove port | HY2.0-4P, data on GPIO 26 |
| Optional battery | 200 mAh base |
| Form factor | 24 x 24 x 14 mm |
| Status | First-class Lume; not viable as a Director |

The Atom is a **Lume-only** host. With no microphone it cannot run beat detection; with no infra-red transmitter it cannot drive PixMob bracelets directly. What it can do is render incoming light commands on its onboard LED and on an LED strip plugged into its Grove port (see [section 2.6](#26-led-strip)).

The single onboard LED doubles as the **status indicator** in place of the LCD pip the Sticks have:

- **Pulsing green** at 1 Hz - the Atom is alive but has not received any frames from a Director yet. The auto-channel scan is running.
- **Solid green for a second** - first frames just arrived. Lock acquired.
- **Wash / pulse colours** - after the lock window, the LED takes part in the show like any other pixel on the strip.

Short-pressing the front button while in Lume mode cycles the LED-strip brightness through 50 / 25 / 10 / 1 percent (see [section 4.5](#45-system)). The same brightness control is also available via the Config menu on the Sticks. The Atom does not have a Menu mode (there is no display to show one); to reach any setting, change it on a Stick - settings are stored per-device in NVS.

The 200 mAh battery base gives a couple of hours of runtime depending on strip brightness and chain length. For longer runs, plug the Atom into a USB power bank.

**Configuring the Atom's strip**: because the Atom has no Config menu, the only way to set chain size and group size is via `platformio.ini` build flags. The `[env:m5stack-atomlite]` block holds four `-DNOCT_DEFAULT_STRIP_*` macros - edit them to match the deployment, reflash, and the values land in NVS authoritatively. See [section 3.5](#35-strip-configuration-build-flags).

### 2.6 LED strip

The firmware drives any SK6812 / WS2812-family addressable LED strip wired to the Grove port. M5Stack sell SK6812 flex strips in five lengths: 10 cm (15 LEDs), 20 cm (29 LEDs), 50 cm (72 LEDs), 1 m (144 LEDs), and 2 m (288 LEDs). All five are supported. The Atom Lite, the Plus2 and the S3 can all drive a strip; the Atom adds its onboard LED to the chain so the show extends seamlessly across the device and the strip.

The strip responds to the same wash and pulse cues as every other Lume in the fleet: a `quiet_wash` cue paints the strip a colour; a `LIGHT_PULSE` cue sparkles a fraction of the pixels per the cue's `CHANCE` probability field. The configurable **group size** (see [section 4.3](#43-connectivity)) controls how the sparkle is distributed across the strip:

- `1` - every LED rolls its own probability die (matches the Tildagon perimeter ring's per-LED sparkle).
- `12` - groups of 12 LEDs flash together as a unit (a Tildagon-ring-sized block on a longer strip).
- A group size equal to the chain length - the whole strip flashes or stays dark as one unit (PixMob-bracelet style).

Default group size is 12. Default brightness is **1 percent** — deliberately conservative so a fresh out-of-box device cannot brown out on any power source we ship (battery, USB-CDC laptop, wall charger), regardless of which mode (Pulse / Whiteout test, raw-RGB white, music show) the operator boots into. A 30-pixel SK6812 strip at full white (RGB 255,255,255) draws ~60 mA per pixel = 1.8 A at 100 % brightness, far beyond any reasonable USB or battery supply; 1 percent keeps peak draw under ~20 mA on a 30-pixel chain. The operator dials up via Config > LED Strip when a heavier supply is available. The four levels are tuned for typical power sources: **50 %** for wall-powered Sticks (DMX bridge / stage rig), **25 %** for USB-CDC laptop or healthy battery, **10 %** any-supply safe, **1 %** ambient hint / fresh-device default. 100 percent was retired 2026-06-23 after bench-confirmed brownout reboots; the 1 percent default was set 2026-06-24 after Pulse / Whiteout tests showed brownouts at 10 percent (root cause: the strip render path ignored the persisted brightness in every mode except Lume — now centralised in `DAL::apply_persisted_strip_settings()` so every mode honours the Config-menu cap).

**Wiring**: the strip plugs into the Grove port via its bundled HY2.0-4P pigtail. Per-host data-line GPIOs are:

| Host | Grove data pin |
|---|---|
| M5StickC Plus2 | GPIO 32 |
| M5StickS3 | GPIO 9 |
| M5Atom Lite | GPIO 26 (Grove) and GPIO 27 (onboard) |

The strip's white-PCB end is the input; chain extra strips off the black-PCB end. The driver allocates buffer space for up to 288 pixels at boot, so changing the chain size in Config takes effect immediately without re-flashing.

On hosts without a Config menu (the Atom Lite), the strip is configured at build time via `platformio.ini` - see [section 3.5](#35-strip-configuration-build-flags).

---

## 3. Installing the firmware

### 3.1 Prerequisites

- A USB-C cable that supports data (cheap charging-only cables will not work).
- A clone of the repository: `git clone https://github.com/ratcliffej/nocturnation-stickc`.
- [PlatformIO](https://platformio.org/) installed. The project assumes the CLI tool is reachable; on macOS the executable is typically at `~/.platformio/penv/bin/pio`.
- An M5StickC Plus2, M5StickS3, or M5Atom Lite.

### 3.2 Building

The project ships three PlatformIO environments, one per supported board:

| Environment | Target |
|---|---|
| `m5stack-stickcplus2` | M5StickC Plus2 |
| `m5stack-stickcs3` | M5StickS3 |
| `m5stack-atomlite` | M5Atom Lite (Lume only) |

Build:

```sh
pio run -e m5stack-stickcs3
```

Or to build both:

```sh
pio run
```

Build artefacts end up under `.pio/build/<env>/firmware.elf` and `.bin`.

### 3.3 Flashing

With the Stick connected by USB-C:

```sh
pio run -e m5stack-stickcs3 -t upload
```

The first flash on a fresh Stick may require holding the lower side button while you plug in the USB cable, to enter the ROM bootloader. Subsequent flashes can be done while the firmware is running; PlatformIO will reset the chip into bootloader mode automatically.

To watch the serial console after flash:

```sh
pio device monitor -e m5stack-stickcs3
```

Press `Ctrl-C` to exit.

### 3.4 Recovering a soft-bricked Stick

If you flash bad firmware and the Stick will not respond, hold the lower side button (BtnA on the Plus2, ButtonA on the S3) for ten seconds with the USB cable disconnected; this triggers a hard reset. If that fails, plug in USB while holding the lower button to force the ROM bootloader, then re-flash.

The firmware never writes to flash regions outside of its own partition table. Bricking the bootloader itself is not possible from a normal `pio run -t upload`.

### 3.5 Strip configuration build flags

The LED-strip settings (enable, brightness, group size, chain size) are first-boot defaults baked into the firmware at compile time. On a Stick they're only the fallback - the Config menu writes NVS at runtime and the menu's value wins on every subsequent boot. On the Atom Lite, where there is no Config menu, the build flags ARE the configuration: change them, reflash, the device runs with the new values.

Each environment in `platformio.ini` carries four `-DNOCT_DEFAULT_STRIP_*` macros:

```ini
[env:m5stack-atomlite]
build_flags =
    ${env:firmware-base.build_flags}
    -DNOCT_DEFAULT_STRIP_ENABLED=1
    -DNOCT_DEFAULT_STRIP_BRIGHTNESS=10
    -DNOCT_DEFAULT_STRIP_GROUP_SIZE=12
    -DNOCT_DEFAULT_STRIP_CHAIN_SIZE=29
    -DNOCT_STRIP_FORCE_DEFAULTS=1
```

| Macro | Range | Meaning |
|---|---|---|
| `NOCT_DEFAULT_STRIP_ENABLED` | 0 / 1 | Master enable for the strip render path |
| `NOCT_DEFAULT_STRIP_BRIGHTNESS` | 0..100 | Per-cent device brightness (cycled by Btn1 in Lume mode) |
| `NOCT_DEFAULT_STRIP_GROUP_SIZE` | 1..255 | Pixels per CHANCE-roll group |
| `NOCT_DEFAULT_STRIP_CHAIN_SIZE` | 1..288 | Physical strip length in LEDs |
| `NOCT_STRIP_FORCE_DEFAULTS` | absent / 1 | When set, treat this build's values as authoritative on flash |

**`NOCT_STRIP_FORCE_DEFAULTS`** is the override flag. Without it set, the macros only matter when NVS is empty (a fresh device or post-factory-reset); after that the operator's Config-menu changes persist. With it set, the firmware compares its embedded build tag (`__DATE__ __TIME__`) to the tag stored in NVS on each boot - if they differ, it writes all four defaults to NVS and updates the tag. This makes every reflash authoritative: whatever the operator had configured at runtime gets replaced by the build's values, exactly once per fresh build.

Per-env defaults shipped today:

| Environment | `FORCE_DEFAULTS` | Rationale |
|---|---|---|
| `m5stack-stickcplus2` | off | Stick has a Config menu; runtime operator wins |
| `m5stack-stickcs3` | off | Same |
| `m5stack-atomlite` | **on** | No Config menu; reflash is the only configuration surface |

To configure an Atom for a deployment, edit the four `-DNOCT_DEFAULT_STRIP_*` values in `[env:m5stack-atomlite]`, run `pio run -e m5stack-atomlite -t upload`, and the device boots with the new values in NVS. Repeat per Atom in the batch.

To use the override flag on a Stick for a deployment-time reset (e.g. "every Stick in the batch must start with chain = 29"), uncomment the `-DNOCT_STRIP_FORCE_DEFAULTS=1` line in the Stick env, flash, then optionally remove the line and re-flash to allow runtime overrides again.

---

## 4. Configuration

The configuration tree is reached by long-pressing Button 1 from any mode. Use Button 1 to advance through items (or to increment a value), Button 2 to drill into a sub-menu or commit a value, and a long press on Button 2 to back out one level.

### 4.1 Top level

The top of the tree has six items:

| Item | Type | NVS key | Default |
|---|---|---|---|
| `Group: N` | Direct action: cycles the Lume receive-filter group | `slv_group` | Random 1, 2, or 3 (assigned on first boot) |
| `Show` | Picker over registered shows | `active_show` | "simple-beat" |
| `Display` | Sub-menu | - | - |
| `Connectivity` | Picker over transports | - | - |
| `Utilities` | Picker over auxiliary tools | - | - |
| `System` | Sub-menu | - | - |

The `Group: N` item is the most-used setting. It is the device-wide receive filter for the Lume (or for the Director when the Director is itself rendering as a Lume under loopback). It interacts with the Director's target group as follows:

- A Director broadcast (`target_group = 0`) renders on every device regardless of the device's own group setting.
- A targeted Director fire (`target_group = N`, with N from 1 to 255) renders only on devices whose `Group: N` setting matches.
- A device whose own `Group` is 0 only renders broadcasts; it does not act as a receive-side wildcard.

First-boot devices are assigned a random group from 1, 2, or 3 so a fleet of newly-flashed Sticks distributes naturally across the three drum groups used by the Dynamic show (kick → group 1, snare → group 2, hi-hat → group 3). The operator can override this from the menu - cycle through 0 to 255, or factory-reset to re-roll.

### 4.2 Display

A single toggle:

- **Pulse Enable** - whether the local LCD shows a pulse animation in sync with light commands. NVS key `scr_puls_en`, default on.

### 4.3 Connectivity

A picker leading to four sub-menus:

**IR** (active):
- `Enable / Disable` - master Director IR transmit and Lume IR transmit toggle. NVS key `ir_en`, default on.
- `Internal` - whether the built-in IR LED fires (Plus2 GPIO 19, S3 GPIO 46). NVS key `ir_int_en`, default on. **Plus2 only** in the menu - shown only on boards where a second emitter is available.
- `External` - whether an external IR transmitter on the Plus2's GPIO 26 header pin fires. NVS key `ir_ext_en`, default off. **Plus2 only** in the menu. With both `Internal` and `External` on, every encoded command is fired through both emitters back-to-back, roughly doubling coverage (see [section 2.3](#23-ir-radiation-patterns)).
- `Protocol` - currently PixMob only; reserved for future protocols.

**ESP-NOW** (active):
- `Director Channel` - selects 1, 6, or 11. NVS key `mst_chan`, default 1.
- `DirID` - this Director's Performance-range source id (one byte in `0x40..0xFE`). Shown as `P:nn`. Random on first install, persisted to NVS (key `mst_pid`), sticky across reboots. A-click drills into a hex editor with three cursor positions cycled by Btn2: high nibble (cycles `4..F`), low nibble (cycles `0..F` skipping the reserved `0xFF` slot), and `Re-roll` (rolls + persists a new random). B-hold exits. New value applies at the next Director start. The byte identifies *this* Director on the wire so Lumes can lock to it; the value is broadcast on every frame and is what the Lume's TOFU lock pins onto. Upstream show logic (Tildagon shows, the orchestrator) can pattern-match on the locked Director's ID to choose content (e.g. a stage-D logo when locked to `0xD0`, an artist QR code when locked to `0xA1`). The id matters *because* it's stable and operator-settable; pin a known value per stage / per artist when content depends on it. See [section 4.7](#47-multi-show-partitioning).
- `Lume Channel` - 0 (auto-scan), 1, 6, or 11. NVS key `slv_chan`, default 0.
- `Lume Repeat` - whether the Lume retransmits accepted frames as a repeater. NVS key `slv_repeat`, default off.

**WiFi** (stub, reserved for future Epic):
- Enable, SSID, Password, Soft-AP mode. Not functional in v0.6.

**DMX** (stub, reserved for Epic 7):
- Carrier, Universe ID, Channel mapping. Not functional in v0.6.

**LED Strip** (active on hosts with a strip wired in - Atom Lite, Plus2, S3):
- `Enable` - master gate on the LED-strip render path. NVS key `strip_en`, default on. When off, the driver drops all events; nothing reaches the strip.
- `Brightness` - uniform multiplier on the wash and pulse render. Cycles 50 / 25 / 10 / 1 percent. NVS key `strip_bri`, default **1** (deliberately conservative; see [section 2.6](#26-led-strip)). The same control is also available via a short-press of Button 1 in Lume mode (the Atom Lite's only adjustment surface).
- `Group size` - pixels per CHANCE-roll group. Cycles 1 / 6 / 12 / 24. NVS key `strip_grp`, default 12. See [section 2.6](#26-led-strip) for the operator-meaningful values.
- `Chain size` - physical strip length plugged in. Cycles 10 cm (15) / 20 cm (29) / 50 cm (72) / 1 m (144) / 2 m (288). NVS key `strip_cnt`, default 29.

### 4.4 Utilities

A picker leading to two sub-menus:

**PixMob**:
- `Set Group ID` - tools for verifying and re-confirming a bracelet's factory group (passive listening via the IR receiver on the S3).
- `Group Target Test` - fire a known colour at a single group, useful for confirming coverage.

**Level Tuning**:
- A multi-mode microphone calibration tool. Choose Live (real-time audio bars), or one of the fixed-percentage modes (25%, 50%, 75%, 100%) for calibration sweep work.

### 4.5 System

- Battery readout
- Firmware version (currently `v0.6`)
- Factory reset (clears the entire `noct` NVS namespace; requires a long confirmation press)

### 4.6 Persistence model

All configuration lives in a single non-volatile-storage namespace called `noct`. Power-cycling preserves every setting. The `System > Factory Reset` action erases the namespace; the firmware then comes up with the defaults shown in the tables above.

Some legacy keys are migrated on first boot after a firmware upgrade. The `slv_ir_grp` key from before Epic 4.65 is dropped (its function moved to the Lume's `slv_group` filter); the legacy visualisation id keys (`active_vis` with values "beat-pulse" or "spectrum-bars") are migrated to the new `active_show` key with value "simple-beat".

### 4.7 Multi-show partitioning

When two or more NocturNation Directors broadcast on the same channel (typical at a festival with parallel stages on channel 11), each Director is identified by a unique one-byte source id in the Performance range `0x40..0xFE`. The id is set per device via [Connectivity > ESP-NOW > DirID](#43-connectivity); it's random on first install, sticky across reboots, and operator-settable to a specific value.

**How the partitioning works:**

- Every frame a Director broadcasts carries its source id in the header.
- A Lume locks (TOFU - Trust-On-First-Use) to the source id of the first valid frame it sees on its current channel after scan or rescan.
- All subsequent frames from any *other* source id are silently dropped at the Lume.
- The lock expires after 10 seconds of silence from the locked source; the next inbound frame re-locks.

So at a multi-stage venue, two Lumes can be physically next to each other and render two different shows depending on which Director each happens to lock to. The split is statistically even if both Directors are equally loud.

**Display content (lyrics, bitmaps) follows the same partitioning.** The Director Stick re-stamps the source id of orchestrator-bridged display frames so they carry the Director's id rather than the broadcast slot. A Lume locked to Director A then drops Director B's lyric overlays cleanly.

**The DirID as upstream tagging hook.** The id is a *value* that upstream show logic (Tildagon shows, the orchestrator) can pattern-match on to choose content:

- A Tildagon show can render a stage-D logo on the LCD background when its TOFU lock reports `0xD0`.
- A different show can render an artist-specific logo when locked to `0xA1`.
- The orchestrator can pick cue files keyed by Director id.

In-show content is restricted to logos / brand images. QR codes are deliberately out of scope for live use: a white scannable panel on every audience badge would tear focus from the stage and break the dark-venue immersion. The QR library bundled with the Tildagon firmware (`uQR.py`) remains in place for operator-facing utilities (help screen) but is not used in the show-content layer.

This only works when the id is *stable and knowable*. The operator pins it via the Config menu's hex editor at the start of a deployment and (in the conventional case) leaves it alone. Conventions like "stage D = `0xD0`", "stage M = `0xMD` shape" etc. are deployment-local choices; the firmware doesn't enforce any particular mapping.

**Operator workflow on a fresh device:**

1. Flash the firmware. The DirID is rolled randomly on first boot and persisted.
2. If the deployment expects a specific value (e.g. for content-tagging), open `Config > ESP-NOW > DirID` and use the hex editor to set it. Random re-roll is also one click away inside that screen.
3. Note the value (it's shown in the menu as `P:nn`) and pass it to whatever upstream consumer needs it.
4. Reboot, or exit and re-enter Director mode, for the new id to take effect on the wire.

---

## 5. Modes and shows

### 5.1 Boot flow

When the Stick powers on, the firmware runs through a fixed sequence:

1. **Splash** - the NocturNation brand-mark with a breathing N (orange-yellow at roughly 2 second period). Three seconds.
2. **Last-mode resume** - the firmware reads the `last_mode` non-volatile-storage key and enters that mode. The factory default is `Director`.

To interrupt the boot resume and pick a different mode, press the lower button during the splash. The firmware will drop into the **Menu** mode (the navigation hub) instead.

### 5.2 Modes

The mode finite-state-machine has six entries:

| Mode | Numeric id | Role |
|---|---|---|
| `Boot` | 0 | Transient countdown |
| `Menu` | 1 | Navigation hub |
| `Director` | 2 | Director-side performance (the default) |
| `Lume` | 3 | ESP-NOW receiver |
| `Config` | 4 | Settings tree (described in [section 4](#4-configuration)) |
| `Test` | 5 | Manual test harness |

To switch between modes, enter `Menu` (either at boot via the splash interrupt or by long-pressing both buttons together from any mode) and pick.

### 5.3 Director mode

This is where most of the action happens. The Director analyses microphone audio, runs the currently-selected show, and fires light commands. The screen displays a label for the active show plus the Director overlay (heartbeat indicator, battery, currently-active show name).

To pick a different show, long-press Button 2; the show picker rotates through every registered show. To open the per-show settings page (where you can change show-specific properties like Simple Beat's colour or Dynamic's groups property), long-press Button 1 once a show is active.

The system currently ships two shows:

**Simple Beat** (id `simple-beat`):
A faithful re-implementation of the pre-Epic-4.7 BeatPulse behaviour. Single colour, fires a one-shot envelope on every kick. The colour is operator-selectable via the show settings page; values are Off, Red, Green, Blue, Yellow, White. When Off is selected the show fires an RGB-zero command on every kick, which renders as nothing visible to the bracelets but exercises the IR path so latency is identical to the lit colours.

**Dynamic** (id `dynamic`):
The FFT-driven show from Epic 4.7. Maps spectral centroid to hue (warm to cool as the spectrum brightens), broadband energy to value (dim to bright), and density to the chance gate (sparse to dense). Routes kick detections to PixMob group 1, snare detections to group 2, hi-hat detections to group 3, when its `groups` property is set to 3. With `groups` set to 1 (the default), all detections are routed to group 0 (broadcast), which works on any deployment without bracelet pre-programming.

The Dynamic show's `groups` property is the most useful setting to know about: leave it at 1 for ordinary deployments where bracelet groups have not been controlled; raise it to 3 if you have manually distributed bracelets across groups 1, 2, and 3 and want to see the kick-snare-hihat split.

### 5.4 Lume mode

The Lume does very little. It listens on its configured channel (or auto-scans), accepts light commands whose target class and group match its configured filter, and fires them through its local infra-red transmitter. The screen shows a small status pip (solid when receiving, hollow when idle) and a sequence-loss signal-quality strip across the top. If no traffic arrives for three seconds, the strip clears and the screen reads NO SIGNAL.

The Lume does not improvise on Director loss. It does not promote itself to Director. It does not run any audio analyser locally.

### 5.5 Test mode

A grid of manual fires for verifying hardware. Each tile fires a single test pattern through the canonical render path; the underlying transmission is identical to what a show would do.

Test patterns include Pulse (single colour, finite envelope), Fade (gentle envelope), Rainbow (continuous high-cadence hue cycle), Sparkle (white twinkle, roughly 0.9 Hz, twenty percent chance per fire), and Whiteout (one-shot bright white). Use Test mode to verify a fresh deployment - if Pulse and Sparkle work, the entire transmission path is healthy.

### 5.6 Config mode

Described in [section 4](#4-configuration).

### 5.7 Menu mode

A scrolling list of every mode plus the entries for Test and Config. Used when you need to jump between modes without rebooting.

---

## 6. Troubleshooting

### 6.1 No bracelet response

**Symptom**: bracelets are receiving the infra-red command (you can see the bracelet wake briefly) but the colour is wrong or the envelope is truncated.

Most often a transient state-residue problem on the bracelet ([section 1.5](#15-bracelet-timing-and-residual-state)). Verify:

- Test mode's Rainbow pattern looks clean. Rainbow has the highest fire cadence and is the most forgiving of residue, so if Rainbow is also misbehaving the issue is elsewhere (low batteries, wrong group, Director IR transmitter blocked).
- Whichever show is misbehaving is using an envelope length that fits inside its fire cadence. If you have customised envelope or cadence values, lengthening the fire cadence or shortening the envelope usually clears the artefacts.

If a particular show looks fine on most bracelets but wrong on one or two, those bracelets may have low batteries; swap the AAA cells.

**Symptom**: no bracelets respond at all.

Check, in this order:

1. **Director is firing**: serial console shows `[espnow TX LIGHT]` lines on every kick.
2. **Lume is receiving**: status pip on the Lume screen is solid, not hollow.
3. **IR is enabled on the Lume**: `Connectivity > IR > Enable` is on.
4. **Group filter matches**: the Director is broadcasting to group 0 (this is the default; check that no over-zealous configuration set the Dynamic show's `groups` to 3 with no group 1/2/3 bracelets distributed).
5. **Line of sight**: the Lume's IR LED must see the bracelets. Walls, large bodies, and even fabric strongly attenuate infra-red.

### 6.2 NO SIGNAL on the Lume

**Symptom**: Lume screen reads NO SIGNAL.

The Lume has gone three seconds with no traffic. Causes, in rough order of likelihood:

1. **Director is off or in a different mode**: enter Director on the Director.
2. **Channel mismatch**: Director is on channel 1, Lume is locked to channel 11 (or vice versa). Either set both to the same channel, or set the Lume to 0 (auto-scan).
3. **Director is very quiet**: Director only broadcasts on beats, and heartbeats are at 1 Hz with a skip-if-recent rule. A silent room with no detected beats and no recent fires can briefly trip the NO SIGNAL threshold; this is benign and resolves as soon as music plays.
4. **Radio range exceeded**: try moving the Lume closer. If the Lume starts working at half the distance, you have a range issue. Solutions: orient the Lume for a clearer line of sight to the Director; enable `Lume Repeat` on an intermediate Lume; reduce concrete walls in the path.

### 6.3 Wrong show running

The currently-active show is shown on the Director overlay. To change it, long-press Button 2 in Director mode. The selection persists in non-volatile storage as `active_show`.

### 6.4 Bracelets respond to one show but not another

Almost always a `chance` gate issue. Some show patterns deliberately fire with a low probability (the Sparkle pattern fires at sixteen percent chance; the Dynamic show modulates chance with spectral density and can drop to roughly four percent on smooth pads). The bracelets that "did not respond" rolled against the chance gate and lost. This is a feature.

If you want to verify a deployment without any chance gating, use Test mode's `Pulse` or `Whiteout` patterns, which fire at one hundred percent chance.

### 6.5 Lume repeater not working

The Lume-as-repeater toggle requires the Lume to actually have received a frame before it can rebroadcast. If the Lume is not receiving (signal-quality strip is clear), it cannot repeat. Verify direct Director-to-Lume reception first; turn the repeater on after.

### 6.6 Low battery behaviour

The Stick's PMIC manages low-battery cutout. When the cell drops below a safety threshold, the chip resets and refuses to boot until external power is applied. The bracelets have no such cut-out; they just fade. A bracelet that responds dimly is on a flat cell.

### 6.7 Audio not detected

If Director is running but no `[espnow TX LIGHT]` lines appear on the serial console even with loud music nearby, the microphone is not seeing audio. Causes:

- The Plus2's PDM microphone is on the back face; if the device is screen-down on a table the microphone is muffled. Stand the Stick on its butt.
- The S3's I2S microphone is on the top edge; less directional but still benefits from a clear sound path.
- Long press into Config and visit `Utilities > Level Tuning > Live`. The on-screen bars should respond to ambient noise. If they are flat, the microphone path is broken at a hardware level.

---

## 7. Glossary

**BeatDetector** - the Director's kick-drum onset detector. Watches low-frequency bands for energy spikes against a one-second running mean. See [section 1.3](#13-how-the-director-decides).

**BtnA, BtnB** - the two user buttons on a Stick. Button 1 is the lower button; Button 2 is the upper button. The S3's power button is owned by hardware and unreachable.

**Bracelet** - a passive infra-red receiver worn by an audience member. Reference target is the PixMob Aurora. See [section 2.2](#22-pixmob-bracelets).

**Chance gate** - a probabilistic filter applied to each infra-red command. A bracelet receiving a command with chance 16 rolls a sixteen-percent die and only renders on a hit. Independent dice per bracelet.

**Class** - one byte (`target_class`) carried in every render command. Identifies the device kind that should accept the command. See [section 1.6](#16-class-and-group-addressing).

**DropDetector** - the Director's structural-event detector. Compares two-second short-window energy to ten-second long-window energy, fires DROP or BREAKDOWN on threshold crossings. See [section 1.3](#13-how-the-director-decides).

**Dynamic** - the FFT-driven show. Maps spectrum analysis to HSV colour and per-drum-group routing. See [section 5.3](#53-director-mode).

**ESP-NOW** - the wireless protocol used between Sticks. Connection-less broadcast on the 2.4 GHz band; vendor-specific 802.11 action frames. See [section 1.7](#17-the-wireless-link).

**Frame** - one transmission unit on either the ESP-NOW link (between Sticks) or the infra-red link (between Sticks and bracelets). See the [protocol manual](protocol-manual.md) for byte-level specifications.

**Group** - one byte (`target_group`) carried in every render command. Identifies a subset of devices within a class. See [section 1.6](#16-class-and-group-addressing).

**Heartbeat** - a one-byte transmission from Director to Lumes at 1 Hz (suppressed if other traffic was recent), indicating Director is alive. See [section 1.8](#18-the-heartbeat).

**HSV** - Hue, Saturation, Value colour model. The Dynamic show works in HSV internally and converts to RGB at the wire.

**LED strip** - any SK6812 / WS2812-family addressable strip wired to a host's Grove port. Rendered as a row of independent pixels by the same light commands that drive bracelets and Lume displays. See [section 2.6](#26-led-strip).

**LIGHT_COMMAND** - one of the two active ESP-NOW message types (alongside `HEARTBEAT`). Nine-byte payload: class, group, RGB, attack/sustain/release/chance. See the [protocol manual](protocol-manual.md).

**Loopback** - the Director's habit of treating itself as one of its own Lumes. The dispatch path routes every light command back through the Director's own infra-red transmitter, screen pulse, and (where wired) LED strip, so the Director can illuminate nearby bracelets and show the cue on its own surfaces.

**Director** - the Stick that listens to audio and decides what lights should do. Runs a Show, fires light commands, and is the default boot mode. Exactly one Director per deployment. See [section 1.1](#11-what-nocturnation-is) and [section 5.3](#53-director-mode).

**M5Atom Lite** - a third reference host alongside the Sticks. ESP32-PICO-D4, sugar-cube form factor, one programmable button, one onboard SK6812 RGB LED, Grove port. Lume-only (no display, no mic, no IR). See [section 2.5](#25-m5atom-lite).

**M5StickC Plus2** - the first-generation reference Stick. ESP32-PICO-V3-02, PDM microphone, omnidirectional IR. End-of-life from M5Stack but fully supported.

**M5StickS3** - the second-generation reference Stick. ESP32-S3-PICO-1-N8R8, I2S codec microphone, focused IR. Current recommended hardware.

**NocturNation** - this project. An open-source distributed crowd-lighting system. Repository: [github.com/ratcliffej/nocturnation-stickc](https://github.com/ratcliffej/nocturnation-stickc).

**NO SIGNAL** - the Lume-screen indication that no Director traffic has arrived for three seconds. See [section 6.2](#62-no-signal-on-the-lume).

**NVS** - Non-Volatile Storage. The ESP32's flash-backed key-value store. NocturNation uses the namespace `noct`. See [section 4.6](#46-persistence-model).

**PixMob** - the manufacturer of the reference bracelets. Their Aurora is the target.

**Show** - a plug-in that produces light commands from analyser events. Lives in `src/shows/`. Operator-selectable from Director mode via long-press Button 2.

**Simple Beat** - the faithful re-implementation of the pre-Epic-4.7 BeatPulse behaviour. Single colour, fires on every kick.

**Lume** - a Stick that listens on the ESP-NOW link and re-fires light commands as infra-red. See [section 1.1](#11-what-nocturnation-is) and [section 5.4](#54-lume-mode).

**Spectrum** - the thirty-two-band log-spaced output of the Director's FFT. Updated every twenty-five milliseconds; consumed by the BeatDetector, DropDetector, and music descriptors.

**Stick** - colloquial for either the M5StickC Plus2 or M5StickS3.

**Test mode** - the manual fire harness. See [section 5.5](#55-test-mode).

---

## 8. Index

This index lists significant defined terms and concepts. For run-time configuration items, see also [section 4](#4-configuration).

| Term | Section |
|---|---|
| Director | [5.3](#53-director-mode) |
| BeatDetector | [1.3](#13-how-the-director-decides) |
| Bracelet (PixMob Aurora) | [1.4](#14-how-the-bracelets-work), [2.2](#22-pixmob-bracelets) |
| Bracelet residue | [1.5](#15-bracelet-timing-and-residual-state) |
| Chance gate | [glossary](#7-glossary) |
| Class+group addressing | [1.6](#16-class-and-group-addressing) |
| Configuration menu | [4](#4-configuration) |
| DropDetector | [1.3](#13-how-the-director-decides) |
| Dynamic show | [5.3](#53-director-mode) |
| ESP-NOW transport | [1.7](#17-the-wireless-link) |
| Firmware flashing | [3.3](#33-flashing) |
| Group filter | [4.1](#41-top-level) |
| Heartbeat | [1.8](#18-the-heartbeat) |
| IR radiation patterns | [2.3](#23-ir-radiation-patterns) |
| Modes (boot, Director, Lume, etc.) | [5.2](#52-modes) |
| NO SIGNAL | [6.2](#62-no-signal-on-the-lume) |
| NVS persistence | [4.6](#46-persistence-model) |
| Quickstart | [quickstart](#quickstart) |
| Repeater (Lume) | [4.3](#43-connectivity) |
| Show picker | [5.3](#53-director-mode) |
| Simple Beat show | [5.3](#53-director-mode) |
| Lume mode | [5.4](#54-lume-mode) |
| Splash | [5.1](#51-boot-flow) |
| Test mode | [5.5](#55-test-mode) |
| Troubleshooting | [6](#6-troubleshooting) |

---

## Acknowledgements

The PixMob infra-red encoding is reverse-engineered from upstream work by [James Wilson (jamesw343)](https://github.com/jamesw343/PixMob_IR) and contributors. NocturNation parity-tests every transmitted byte against a Python reference encoder to keep behaviour locked to that upstream.

A huge thank-you to PixMob and Xylobands for their pioneering work in making the audience part of the show.

Hardware abstraction patterns and many small implementation details follow the conventions of the [M5Unified](https://github.com/m5stack/M5Unified) library by M5Stack.

The audio analyser tuning history (BeatDetector and DropDetector) reflects bench iteration against a deliberately mixed-genre playlist; thanks to the test listeners who sat through the longer parameter sweeps.
