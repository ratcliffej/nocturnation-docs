# NocturNation QLC+ fixtures

QLC+ fixture definitions (`.qxf` files) for the NocturNation Lume
fleet. Drop these into QLC+'s user-fixtures directory, restart QLC+,
and they appear under the **NocturNation** manufacturer in the
fixture browser.

## Files

| File | Use when |
|---|---|
| `nocturnation-lume-group-v2.qxf` | **Current.** 40 channels per block, supports broadcast + 6 addressable groups, with Raw RGB direct-control channels (v0.3.0). |
| `nocturnation-lume-group.qxf` | Legacy 12-channel fixture from Epic 4. Keep around for older saved workspaces; new work should use v2. |

## v2 block layout (40 channels per fixture instance)

The fixture is a **40-channel block** that you patch *once per Lume
group* you want to address independently:

| Base address | Targets |
|---|---|
| 1 | Broadcast (all Lumes, every group) |
| 41 | Group 1 |
| 81 | Group 2 |
| 121 | Group 3 |
| 161 | Group 4 |
| 201 | Group 5 |
| 241 | Group 6 |

Within each block, channels 1-27 are active (the firmware reads them);
28-40 are reserved padding so the next block patches cleanly at the
spec'd base address. The channel layout, block-local:

| Channel | Name | Notes |
|---|---|---|
| **1** | **Master Intensity** | Scales every output for this block — pulse RGB, wash intensity, **and raw RGB**. Acts as a dimmer in standard LD-fixture convention. |
| 2 | Strobe Rate | 0 = off; 1-255 = 0..4 Hz pulse cadence (Harding-safe cap). |
| 3-5 | Pulse R / G / B | Sparkle / accent flash colour. |
| 6 | Pulse Trigger | Rising edge ≥128 fires one pulse. Drop below 128 to re-arm. |
| 7-9 | Pulse Attack / Sustain / Release | Each quantises to one of 8 PixMob `Time` buckets (T_0_MS .. T_3840_MS). 32-wide slider buckets. |
| 10 | Pulse Probability | Inverted on the slider so high = more likely to fire. Quantises to 8 PixMob `Chance` buckets (4 %, 10 %, 16 %, 32 %, 50 %, 67 %, 88 %, 100 %). |
| 11-13 | Wash A R / G / B | Start colour of the wash. |
| 14-16 | Wash B R / G / B | End colour (ignored when Wash Cycle = 0). |
| 17 | Wash Cycle | 0 = hold anchor A; 1-255 = 100 ms units (0.1 s to 25.5 s) for the A↔B↔A drift period. |
| 18 | Wash Intensity | Wash brightness; independent of Master so you can fade a wash without affecting pulses. |
| 19 | Wash Attack | 100 ms units (fade-in time). |
| 20 | Wash Release | 100 ms units (default fade-out). |
| 21-23 | reserved | The firmware reads these (legacy TTL + Pulse-Response fields) but the LD doesn't need to touch them. Leave at 0. |
| **24** | **Raw R** | See "Raw RGB direct control" below. |
| **25** | **Raw G** | |
| **26** | **Raw B** | |
| **27** | **Raw Enable** | ≥ 128 → raw RGB takes over; < 128 → FX engine runs. |
| 28-40 | padding | Reserved. Ignored by the firmware. |

## Raw RGB direct control (v0.3.0, EMF stage-team feature)

Channels 24-27 let an LD treat each Lume group as a plain RGB par
fixture, **bypassing the FX engine entirely**. Useful when:

- You want simple on/off colour control without sparkles / strobes /
  wash transitions getting in the way.
- You're cuing static-colour scenes alongside a known music track and
  want the lighting predictable, not music-reactive.
- The Lume group is part of a larger rig where it should match other
  fixtures' behaviour (e.g. a row of RGB pars + Lumes all driven from
  the same scene).

**How it works.** When **Raw Enable** (channel 27) is ≥ 128, the
StickC mapper:

1. Reads Raw R / G / B (channels 24-26).
2. Scales them by Master Intensity (channel 1) — LD's master slider
   dims the raw colour just like any RGB fixture.
3. Emits a **static `LIGHT_WASH`** to the Lume fleet (same wire
   protocol as the FX engine's wash, just with `r1 == r2` and no
   cycle).
4. **Suppresses** the FX engine's pulse / wash / strobe output for
   this block. No surprise flashes during raw control.

When Raw Enable drops below 128, control hands back to the FX engine
cleanly — the bridge re-emits whatever the FX channels are currently
showing, so receivers don't keep displaying the stale raw colour.

**Receiver behaviour:**

- **Tildagon LCD background** — renders the raw colour as a held
  fill.
- **Tildagon perimeter ring** — renders the raw colour on all 12 LEDs.
- **StickC LED strip** (Plus2 / S3 / Atom Lite) — renders the raw
  colour across all pixels.
- **PixMob bracelets** — render the raw colour via periodic IR
  refresh. Note: keep raw RGB values ≥ ~16 if you're targeting groups
  containing PixMobs. Near-black colours (e.g. `5,0,0`) risk putting
  the bracelet into a persistent "stress" state from receiving very-
  dim IR refreshes. Pure black (`0,0,0`) is filtered safely at the
  driver and routed through wash-end instead.

**Scene cookbook.** A scene that lights every Lume blue at 50 %
brightness:

| Channel | Value | What it does |
|---|---|---|
| 1 (Master) | 128 | 50 % brightness |
| 24 (Raw R) | 0 | |
| 25 (Raw G) | 0 | |
| 26 (Raw B) | 255 | |
| 27 (Raw Enable) | 255 | Raw mode on |

To turn the same group off, write 0 to channel 27 (Raw Enable drops
below 128 → control returns to the FX engine). If your scene's FX
channels are also zero, the result is a dark block.

## Lume groups and audience distribution

NocturNation is **audience-owned merch** — punters buy or are given
a NocturNation-flashed badge (Tildagon for EMF) and bring it to the
show. The LD has no opportunity to pre-configure each device by hand.
For independent zone control to be possible at all, the badge fleet
needs to **self-distribute into addressable groups** without any
operator touch.

**How it works.** The first time the NocturNation app runs on a
device, it rolls a random group in **[1, 3]** and persists it. From
then on the value is sticky — the same badge always lands in the
same zone unless the owner explicitly changes it via the in-app
settings menu. Across a large audience this gives:

- **Group 1**: ~33% of the fleet
- **Group 2**: ~33% of the fleet
- **Group 3**: ~33% of the fleet

The distribution is per-device-independent — each badge rolls its
own random value at first run, no fleet-wide correlation.

**Group 0 (broadcast)** is *not* part of the random pick. A device
that has never had the NocturNation app run before will never
default to 0. The only way a Lume ends up at group 0 is the owner
explicitly setting it via the in-app settings menu (or an operator
on a Stick's Config menu).

**What this means for cuing**:

- **Broadcast scenes** (patched at universe address 1, target_group 0)
  reach every Lume in the audience regardless of their personal group.
  Use for full-fleet looks — the entire crowd lights up together.
- **Group-1/2/3 scenes** (patched at 41 / 81 / 121) reach roughly a
  third of the fleet each. Use for back-and-forth callbacks,
  syncopated patterns, "left/right/centre" stage moments.
- **Groups 4-6** (patched at 161 / 201 / 241) reach nobody by
  default. Available for future expansion or operator-assigned
  groups (e.g. a tech crew sub-fleet manually set to group 4).

**Persistence across shows.** A punter's badge keeps its group
forever. If they come back to the next NocturNation show, they're
in the same zone. That's by design — it gives the LD a stable
addressing model across a tour without anyone touching individual
devices.

**Migration / re-roll.** If a punter wants to change zones (or
reset to broadcast), the in-app settings menu is the operator
recourse. The random pick only fires on a never-configured device.

## Per-Stick brightness setting (not a DMX channel)

Each Stick (Plus2 / S3 / Atom Lite) has a **per-device LED-strip
brightness** scaler that the LD does *not* control via DMX — it's
set on the device itself (Config menu on the Stick, or a short
press of the front button in Lume mode). This is a hardware-side
safety cap to prevent the strip from drawing more current than the
device's power supply can deliver.

**Levels** cycle in this order:

| Level | Peak current (30-pixel strip) | When to use |
|---|---|---|
| **50 %** | ~900 mA | Stick on a USB-C charger or powered hub (DMX-bridge / stage rig) |
| **25 %** | ~450 mA | Stick on laptop USB-CDC, or healthy battery |
| **10 %** | ~180 mA | Default for fresh devices; safe on every supply |
| **1 %**  | ~18 mA | Ambient hint / near-darkness |

A 30-pixel SK6812 strip at full white (RGB 255,255,255) draws
~60 mA per pixel — 1.8 A total at 100 % brightness, far beyond
any reasonable USB / battery supply. The brightness scaler is what
keeps the device out of brownout territory.

**LD-facing implication.** When you set Raw R/G/B to 255 and a
Lume looks dim, the per-device brightness is the limiter, not your
universe value. The fix is to bump the device's brightness setting,
not the DMX. The stage-rig Stick should be on 50 % (wall-powered),
audience badges typically run at 10 % (default, battery-safe).

## Patching the fixture in QLC+

In QLC+'s **Fixtures** panel:

1. **Add fixture** → manufacturer **NocturNation** → model
   **Lume Group v2 (DMX bridge)** → mode **Block (40ch)**.
2. Set the base address to one of the values from the table above
   (1 for broadcast, 41 for group 1, etc.).
3. Repeat for each group you want to address independently.

You can patch as few or as many as you need — the typical artist-stage
setup is the broadcast block plus 1-3 group blocks. Heads inside the
fixture give QLC+'s RGB-fixture controls (the colour picker, the
gradient widgets) handles for:

- Pulse R/G/B (channels 3-5)
- Wash A R/G/B (channels 11-13)
- Wash B R/G/B (channels 14-16)
- Raw R/G/B (channels 24-26)

So a colour picker bound to a head selects the right colour for the
right purpose.

## Versioning

Bumping the fixture file's internal `<Version>` rather than renaming
the file means existing scenes keep working — channels stay at the
same numbers, new channels just become accessible. If a future change
needs to *renumber* an existing channel, that warrants a v3 file +
filename change so older workspaces don't silently break.

| Version | Date | Change |
|---|---|---|
| 0.3.0 | 2026-06-23 | Added Raw R/G/B/Enable (channels 24-27) for direct stage-LD control. |
| 0.2.1 | (Epic 7) | 40-channel block; per-group instances; Pulse + Wash with extended params. |
| 0.1 | (Epic 4) | Original 12-channel fixture. (Filename `nocturnation-lume-group.qxf`.) |
