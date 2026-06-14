# NocturNation documentation

> Open-source crowd lighting, conjured from cheap silicon.

This repository is the public documentation for [NocturNation](https://github.com/ratcliffej/nocturnation-stickc), a modular open-source crowd-lighting system: a Director device listens to music, detects beats and structural events, and broadcasts light commands to a swarm of Lume devices that drive PixMob bracelets and badge LEDs worn by the audience.

The documents here are the canonical entry points for operators and implementers. They are mirrored from the project's Notion workspace, which remains the master copy - edits flow Notion → here on a periodic sync. Per-Epic plans, design RFCs and internal working notes stay in Notion and are deliberately excluded from this mirror.

## Documents

| Document | Audience | What it covers |
|---|---|---|
| [User manual](manuals/user-manual.md) | Operators setting up a venue | Theory of operation, hardware, firmware install, configuration walk-through, modes and shows, troubleshooting, glossary. |
| [Operator workflow](manuals/operator-workflow.md) | Operators on the night | Channel selection, Performance Mode (channel 11), `source_id` verification, on-the-night recovery. |
| [Protocol manual](manuals/protocol-manual.md) | Implementers of a transmitter or receiver | ESP-NOW wireless layer, frame formats, class-and-group addressing, PixMob IR annex, channel discovery, NVS schema, conformance, test vectors. |
| [Flow diagrams](manuals/flow-diagrams.md) | Both | Mermaid renderings: topology, boot, modes, analyser, dispatch, receive, routing, configuration, channel discovery. |
| [Developer guide](developing-shows.md) | Contributors writing `Show` plug-ins | The `Show` base class, analyser hooks, `render_fx` API, widget composition, persistence, testing. |
| [QLC+ beginner's guide](qlc-plus-beginners-guide.md) | Operators driving NocturNation from a DMX console | From-zero walkthrough: install QLC+, learn the four panels, define the core DMX concepts, plug a StickC in, programme a Scene, sequence a Chaser, link a cue stack to a track. |
| [Music orchestrator guide](orchestrator-guide.md) | Operators running a programmed show synchronised to music | From-zero walkthrough: install nowplaying-cli, run the orchestrator, author a `.cues` file, file-naming convention, lyric anchors, debug mode. Pairs with the FX library reference. |
| [FX library](fx-library.md) | Operators authoring cue files | Generated reference of every FX the orchestrator can run: parameters, units, defaults. Regenerated from the FX classes by `tools/scripts/gen_fx_library.py`. |
| [Architecture specification](architecture.md) | Designers and curious readers | The full system design. The manuals above are the publishable distillation of this. |
| [DAL design](dal-design.md) | Firmware contributors | Device-abstraction-layer design notes. |
| [HAL design](hal-design.md) | Firmware contributors | Hardware-abstraction-layer design notes. |

## Related repositories

- [`nocturnation-stickc`](https://github.com/ratcliffej/nocturnation-stickc) - reference firmware for the M5StickC Plus2 and M5StickS3 Director/Lume Sticks.
- [`nocturnation-tildagon`](https://github.com/ratcliffej/nocturnation-tildagon) - receiver and manual-Director app for the EMF Tildagon badge.

## Conventions

- Byte values in hexadecimal with the `0x` prefix; ranges inclusive at both ends.
- Normative language in the protocol manual follows RFC 2119: MUST, SHOULD, MAY.

## Licence

The documentation is licensed under [Creative Commons Attribution-ShareAlike 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (CC BY-SA 4.0). The firmware code is MIT; hardware designs, when published, are CERN-OHL-S 2.0.
