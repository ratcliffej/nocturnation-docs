---
title: "QLC+ beginner's guide"
status: Draft
notion_url:
notion_id:
last_synced:
sync_direction: bidirectional
---

# QLC+ beginner's guide

A from-zero walkthrough to driving NocturNation from QLC+, written for someone who has never opened a lighting console before. By the end you will be able to install QLC+, navigate its interface, define the core concepts in plain English, plug a StickC into your laptop, build a Scene, sequence a Chaser, and link a cue stack to a track for a coordinated live performance.

This guide is split across thirteen sections. The first four are concept-only - install QLC+, learn the UI, learn the vocabulary. No NocturNation hardware required for sections 1-4. Section 5 introduces the cable; section 7 is the first-light moment where a slider in QLC+ produces a visible Lume flash.

If you already know QLC+ and want the short version of "where do I point my console?", read [`dmx-quickstart.md`](dmx-quickstart.md) instead.

## Table of contents

- [1. What QLC+ is and why we're using it](#1-what-qlc-is-and-why-were-using-it)
- [2. Install QLC+](#2-install-qlc)
- [3. The four panels](#3-the-four-panels)
- [4. Core concepts](#4-core-concepts)
- [5. Hardware](#5-hardware)
- [6. Configuring QLC+ output](#6-configuring-qlc-output)
- 7\. First light *(after Epic 7 B1-B3 land in the firmware)*
- 8\. Building a Scene
- 9\. Building a Chaser
- 10\. Linking a track
- 11\. Saving + sharing
- 12\. Glossary
- 13\. Further reading

---

## 1. What QLC+ is and why we're using it

**QLC+** (Q Light Controller Plus) is an open-source lighting console. It is the software a working lighting designer reaches for when they sit down to programme a show - the cross-platform, Qt-based equivalent of consoles like ETC Eos, MA Lighting grandMA, or Avolites. Where those cost five to fifty thousand pounds, QLC+ is free.

QLC+ has been in active development since 2004 and is used across hackerspaces, art events, small venues, theatre training programmes, and a growing number of working professional rigs. It speaks the same wire protocols (DMX-512, Art-Net, sACN, MIDI, OSC) as the commercial consoles, so a show built in QLC+ ports cleanly to a venue that uses grandMA on the night.

**Why are we using it for NocturNation?**

The architecture spec is explicit on this: NocturNation is a fixture, not a lighting designer. In autonomous mode (the Show framework) the system runs its own decisions, which is the right answer for hackspaces and home use. But the moment there *is* a real lighting designer in the room - at a venue, a touring show, an art installation programmed to a known track - the LD wants their own console driving the lights, not a black-box autonomous mode. QLC+ is the lingua franca that gets us there: any LD who can drive QLC+ can drive NocturNation, with no NocturNation-specific learning beyond a small fixture file.

The architecture spec calls this the "DMX-driven" mode in §1.2 *Implications for the DMX direction*. This guide is your way into it.

**The mental model.** QLC+ generates a stream of DMX values (numbers between 0 and 255, on numbered channels) and sends them down a wire to NocturNation. The StickC reads those channels, decides what each value means (channel 1 = master intensity, channel 2 = strobe rate, channels 3-5 = pulse RGB, etc.), and broadcasts NocturNation events to the bracelets and Tildagons. From the LD's point of view, NocturNation is a fixture with twelve channels per Lume group - exactly the same shape as a generic RGB par-can or wash light in any other console session.

---

## 2. Install QLC+

> **Status:** Use the official build from [qlcplus.org/downloads.html](https://www.qlcplus.org/downloads.html) (macOS `.dmg`, Windows installer, or Linux package), current stable (4.13.x as of mid-2026). Package-manager installs (Homebrew, apt, etc.) work fine but are a step removed from what the QLC+ project ships and tests.

1. **Download.** Visit [https://www.qlcplus.org/downloads.html](https://www.qlcplus.org/downloads.html), choose the build for your platform and the current stable release, and let it complete. 

2. **Run the installer or open the .dmg.** 

4. **First launch and first run.** QLC+ opens with an empty workspace. There may be a one-time dialog asking which output plugin to enable, or offering to load an example - for now, dismiss anything that pops up. You should end up at the main workspace with several panel-selector buttons (we'll meet them in section 3).

   ![alt text](images/qlc-first-launch.png)

You're installed. **Verify by trying to quit and relaunch** - the second launch should be one double-click.

---

## 3. The four panels

QLC+ presents its interface as a small set of mode panels. Different versions of QLC+ label and arrange them slightly differently, but every modern release exposes the same four primary modes via toolbar buttons (usually along the top of the window). They are:

| Panel | What it is for | Beginner attention |
|-------|----------------|---------------------|
| **Fixtures** | Define the rig - what's connected, where it lives, and how it's addressed. | Start here. Every show begins by telling QLC+ what's in the room. |
| **Functions** | Define behaviour - the library of Scenes, Chasers, Audio, Shows. | Spend most of your programming time here. |
| **Virtual Console** | Build a custom performance UI - sliders, buttons, cue lists you'll trigger live. | Optional but powerful. Defer until you have a Scene to control. |
| **Simple Desk** | A built-in per-channel slider board. Always available; useful for testing. | Reach for this when you want to slam a channel value and see what happens. |

You select between them via the toolbar buttons (in some versions they're tabs along the bottom or icons along the top - same idea either way).

<!-- Screenshot: the QLC+ main window with the four panel-selector buttons annotated. Open QLC+, click each panel in turn, and replace this comment with a single annotated screenshot or one screenshot per panel. -->

A short tour of each - what to look at, what to ignore for now.

### Fixtures

This is where you build your virtual rig. The left pane lists the fixtures you've added; the right pane shows the channel layout for the selected fixture. New installs have nothing here yet. When we add the NocturNation fixture in section 6, this is where it lands.

Also in this panel you'll find universe management (NocturNation only needs one universe for the EMF demo) and the input/output configuration that connects QLC+ to whatever is on the other end of the wire.

### Functions

The library of behaviours QLC+ can produce. The left side lists folders (Scenes, Chasers, Audio, Shows, etc.); the right side opens the editor for whichever function you've selected. You'll build everything here: a static "all-red wash" Scene, a Chaser that cycles through palette colours, a Show that maps cues onto a recorded track's timeline.

### Virtual Console

A blank canvas where you drag in widgets - sliders, buttons, XY pads, cue list windows - and wire them to Functions. The point: a performance-night UI you operate without thinking about the underlying channel structure. For our first cue-stack-against-a-track exercise you can ignore this entirely; the Show timeline editor in Functions will drive playback.

### Simple Desk

Per-channel sliders for the active universe. Drag a slider, that channel's value changes immediately. The fastest way to test "is anything happening at all" - when you first connect a fixture, Simple Desk is where you verify it responds before you bother programming anything in Functions.

**The beginner's path through the panels:** Fixtures (add the NocturNation fixture) → Simple Desk (test it responds to a slider) → Functions (build a Scene, then a Chaser, then a Show) → Virtual Console (optional; only if you want a custom live UI). The next sections follow exactly that order.

---

## 4. Core concepts

DMX has its own vocabulary that's worth getting right before we touch the rest of the tool. Read this section twice if you need to - it's only six terms, but they reappear constantly.

### Universe

A numbered group of **512 channels**. One DMX cable historically carried one universe. QLC+ supports multiple universes side-by-side, routed to different outputs.

For NocturNation, the village-talk demo uses **one universe** routed over the USB-C cable to a single StickC. You don't need more.

*When you reach for this:* once, when you first set up your project. After that, leave it alone.

### Channel

One of the 512 lines inside a universe. Each channel carries an 8-bit value - a single number between **0 and 255**. That's the only data DMX transmits: 512 numbers per refresh, sent again and again about 30-44 times per second.

What a channel "means" is defined by the fixture occupying that channel address. Channel 1 is just "channel 1" until you tell QLC+ "channel 1 is the master intensity of my NocturNation fixture".

*When you reach for this:* in Simple Desk for testing. Otherwise, never directly - your fixtures define names that map to channels for you.

### Fixture

A logical device that occupies **N consecutive channels** starting at a base address. The fixture file (a `.qxf` file in QLC+'s case) tells QLC+ "this device has 12 channels - channel 1 is Master Intensity, channel 2 is Strobe Rate, channels 3-5 are Pulse R/G/B, …". Once added, you address the fixture by its meaningful names instead of channel numbers.

The NocturNation fixture is **12 channels per Lume group**. We'll add it to QLC+ in section 6.

*When you reach for this:* whenever you add or move hardware. One fixture per Lume group in NocturNation's case.

### Scene

A **saved set of channel values** - a single lighting state. "All NocturNation groups: blue at 50 % intensity, wash anchor B = cyan." Once saved, you can recall a Scene with a button, or fade smoothly to it over a chosen time. Scenes are the fundamental building blocks of any show.

*When you reach for this:* every static "look" in your show is a Scene. The verse look, the chorus look, the blackout - each one.

### Chaser

A **sequence of Scenes** with timings - "Scene A for 2 s → Scene B for 2 s → Scene C for 2 s → loop". Chasers are how you build repeating patterns like a colour wheel cycle, a beat-flash sequence, or a build-up.

*When you reach for this:* any repeating motion that's bigger than a single Scene. If you find yourself programming four near-identical Scenes that step through colours, that's a Chaser asking to be born.

### Show

A **timeline** - Scenes and Chasers placed at specific points in time, optionally synchronised to an audio file. This is the editor we'll use to programme a cue stack against a Coldplay track for the EMF village talk.

*When you reach for this:* whenever the lights need to follow recorded audio precisely. The Show editor's timeline view shows the waveform with cues laid against it.

### Cue List

A **numbered list of triggerable cues** - press the **Go** button, advance to cue N+1. The performance interface. Less relevant for our first track-driven show (the Show editor handles playback), but the way most live theatre and concert programming works.

*When you reach for this:* live shows where an operator presses Go in response to what's happening on stage. Optional for the village-talk demo.

### How these connect

A typical flow:

```
Channel  →  Fixture  →  Scene  →  Chaser / Show  →  Cue List
(raw)       (named)     (look)    (sequence)        (live trigger)
```

You set up the rig (Fixtures), define looks (Scenes), assemble them into motion (Chasers) or against time (Shows), and trigger them live (Cue Lists or Virtual Console buttons). For the EMF demo we'll go: Fixtures → Scenes → Show. No Chasers, no Cue List. Minimal viable performance.

---

## 5. Hardware

> **Kit list.** Laptop with QLC+ installed (sections 1-2). One StickC. One USB-C cable that carries data (most do, but some "charge-only" cables don't - if in doubt, use the one that came with the Stick or a known-good USB-C data cable). **No DMX dongle.**

The kit list is unusually short because of how NocturNation's DMX path works. In a traditional rig:

```
laptop  →  USB-DMX dongle  →  XLR-3 or RJ45 cable  →  DMX fixtures
                              (RS-485 wire)            (each speaks DMX-512)
```

In NocturNation's DMX path:

```
laptop  →  USB-C cable  →  StickC
                            (the Stick is the receiver; it speaks
                             the same Enttec Pro framing over USB-CDC
                             that a dongle would translate onto RS-485)
```

The StickC takes the role the dongle + first fixture would normally fill. There's nothing else to buy. This is a property of the architecture, not a workaround.

### Plug in the Stick

Plug the StickC into the laptop via USB-C. The Stick boots normally - you don't need to put it in any special mode. The USB-CDC peripheral comes up alongside the application firmware and the laptop's operating system enumerates the Stick as a serial port.

### Find the serial port

QLC+ in section 6 will need the name of that serial port. The name differs by operating system:

**macOS.** Open Terminal and run:

```sh
ls /dev/cu.usbmodem*
```

You should see one entry, something like `/dev/cu.usbmodem14101` or similar. The exact suffix varies per Mac and per USB port - what matters is that you see *exactly one* entry once the Stick is plugged in, and *no* entries when it's unplugged. If you see zero entries with the Stick plugged in, jump to "Driver troubleshooting" below.

**Linux.** Run:

```sh
ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

You should see one entry, typically `/dev/ttyACM0` (the modern CDC-ACM kernel driver) or `/dev/ttyUSB0` (older USB-serial bridges). Either is fine - note which one and use it in section 6.

**Windows.** Open Device Manager (Start → type `Device Manager`), expand **Ports (COM & LPT)**. You should see an entry like `USB Serial Device (COM3)` or `Silicon Labs CP210x USB to UART Bridge (COM3)`. The `COMx` number is what QLC+ needs.

<!-- Verify-and-screenshot: capture what shows up on your machine when the Stick is plugged in. macOS users: paste the `ls` output. Linux users: same. Windows users: a Device Manager screenshot is more useful than text. -->

### Driver troubleshooting

In most cases the operating system auto-detects the StickC. If your platform doesn't:

- **macOS 10.13+ and current macOS.** Built-in support for CDC-ACM devices; no driver download needed. If the port doesn't appear, try a different USB-C cable (charge-only cables are the most common culprit) and a different USB port.
- **Linux.** The `cdc_acm` kernel module ships with every distro; loaded automatically on plug-in. `dmesg | tail` after plugging the Stick in should show the enumeration. If the device is recognised but `/dev/ttyACM0` isn't readable, your user may need to be in the `dialout` group: `sudo usermod -aG dialout $USER` then log out and back in.
- **Windows.** Some Stick variants enumerate as native USB-CDC (no driver needed on Windows 10+); others use a Silicon Labs CP210x USB-to-UART chip which needs the CP210x VCP driver from [silabs.com](https://www.silabs.com/developers/usb-to-uart-bridge-vcp-drivers). Install the driver, replug the Stick.

<!-- Verify-against-install: which of these applied to your machine? If macOS auto-detected with no fuss, that's the canonical happy path; everything else is troubleshooting noise the reader only needs if they hit it. -->

### Confirm the port is alive

Optional but useful sanity check: read the Stick's serial output to confirm the port is the Stick and not some other USB-serial device that happens to be plugged in.

**macOS / Linux:**

```sh
cat /dev/cu.usbmodem14101    # macOS - use your actual port name
# or
cat /dev/ttyACM0             # Linux
```

You should see Stick boot output and any debug messages the firmware prints. Press `Ctrl-C` to stop. Don't leave this terminal open when you move to QLC+ in section 6 - the serial port can only have one consumer at a time, and you want QLC+ to be the one.

---

## 6. Configuring QLC+ output

> **Heads-up on scope.** Section 6 is *preparation*. After completing it, QLC+ will be configured to point its DMX output at the StickC's serial port, but no light will yet change colour. The first-light moment - where a slider in QLC+ produces a visible Lume flash - is **section 7**, and section 7 depends on Epic 7 firmware blocks B1-B3 landing on the StickC. Until those ship, finish section 6's clicks anyway so the workflow is rehearsed and the configuration is saved.

### Open the Inputs/Outputs panel

In QLC+, open the **Inputs/Outputs** panel. The exact route varies by version:

- Some versions expose it as a button on the main toolbar alongside Fixtures / Functions / Virtual Console / Simple Desk.
- Others put it under **Tools → Plugin Manager** or **Inputs/Outputs** in the application menu bar.
- In recent versions there's also a dedicated **Universes** tab inside the Fixtures panel that shows the same configuration in a different layout.

<!-- Verify-against-install: which route does QLC+ 4.13.x take on your machine? Pick the one that worked and rewrite this section to lead with it; the other routes can become a single "(also reachable via …)" line. -->

You should land on a page with two main columns: **Universe** on the left (a row per universe; new installs have Universe 1 only) and **Output Plugin** alongside it.

<!-- Verify-and-screenshot: capture the Inputs/Outputs panel as QLC+ shows it on first open. -->

### Pick the Enttec DMX USB Pro plugin

Click the **Universe 1** row to select it. In the plugin column or the panel below, choose **Enttec DMX USB Pro** as the output plugin.

> If you don't see Enttec DMX USB Pro in the list, the plugin may be disabled. Check **Tools → Plugin Manager** (or equivalent) and confirm the Enttec plugin is enabled. Restart QLC+ if you toggle it.

<!-- Verify-against-install: confirm the plugin appears in the list. If QLC+ on macOS ships with the Enttec plugin built-in (it usually does), no extra install is needed. If your install is missing it, that's the bigger find - document the install path for it. -->

### Point the plugin at the Stick's serial port

With Enttec DMX USB Pro selected, there should be a **Configure** button (or similar) that opens the plugin's settings. Inside:

- Find the **Serial port** dropdown (or text field).
- Choose the port you identified in section 5:
  - macOS: `/dev/cu.usbmodem14101` (your number will differ)
  - Linux: `/dev/ttyACM0` or `/dev/ttyUSB0`
  - Windows: `COM3` (your number will differ)
- Save / OK to close the plugin config.

<!-- Verify-and-screenshot: the plugin's Configure dialog with the serial port selected. -->

### Address the universe

Back on the Inputs/Outputs panel, the Universe 1 row should now show the Enttec plugin assigned, with the Stick's serial port as the output. That means: any DMX values QLC+ generates for Universe 1 channels 1-512 will be packed into Enttec Pro frames and written down the cable to the Stick.

In a normal DMX rig you'd also set a *base address* on each fixture (channel 1 vs channel 13 vs channel 25, etc.) to slot multiple fixtures into one universe. NocturNation's fixture lands in section 8 once the `.qxf` ships; for now the universe is configured but empty.

### What QLC+ will look like

After section 6, QLC+ should show:

- **Inputs/Outputs panel:** Universe 1 mapped to "Enttec DMX USB Pro - `<your serial port>`".
- **No errors** at the top of the QLC+ window. Some versions of the Enttec plugin do a handshake (send a "Get Widget Serial Number" command, expect a response) at startup. Until Epic 7 firmware lands on the Stick, the Stick won't respond to that handshake and QLC+ may show "device not responding" or "widget not identified" in the plugin's status line. **That is expected at this point** - the configuration is correct; the Stick just doesn't speak Enttec Pro back yet. Section 7 closes that loop.

### Save the workspace

File → Save Workspace. Pick a name like `nocturnation-test.qxw` and save it somewhere you'll find again. Reopening QLC+ and loading this workspace gets you back to this state without redoing the clicks.

### What's next

You've now:

1. Installed QLC+ and learned the four panels (sections 1-3).
2. Defined the core DMX vocabulary (section 4).
3. Plugged a StickC in and found its serial port (section 5).
4. Pointed QLC+'s output at that serial port (section 6).

Section 7 onwards picks up once Epic 7 B1-B3 firmware ships - the Stick will then parse the Enttec Pro frames QLC+ is writing, and a slider in QLC+ will fire a real Lume. Watch the [Epic 7 implementation page](epics/epic-07-dmx-qlc.md) for that landing (the `epics/` folder is maintainer-only; the link is for local clones).

In the meantime, **read sections 1-4 again if anything is hazy**, and try the two pre-hardware exercises that were noted earlier:

1. Open Simple Desk and drag a slider for channel 1. Nothing visible happens (no fixture, no working output yet), but you've now used the interface that's the foundation of everything else.
2. Open Functions and create a Scene with three or four channels set to non-zero values. Don't worry about what they do - just notice how the Scene editor lays out a "saved channel state".

Then come back when section 7 lands.
