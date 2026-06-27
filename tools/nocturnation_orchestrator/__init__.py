"""NocturNation music orchestrator (Epic 10).

Laptop-side daemon that watches OS now-playing state, looks up the
current track in `Docs/songs/<artist>-<title>.yml`, and runs the YAML's
cue list through the FX engine. The engine writes a 512-byte DMX
universe state; the output dispatcher emits it as Enttec Pro frames
over USB direct, or as Art-Net to a co-running shim.

This package hosts:

- `fx/`        - FX engine: `Fx` base, `FxRegistry`, `FxRunner`. FX
                 implementations write into a DMX universe bytearray.
- (future)     - `cues/` cue-list loader, `nowplaying/` OS backends,
                 `output/` dispatcher.

Lumes and the StickC DMX Bridge are unchanged. The orchestrator is a
parallel DMX producer alongside QLC+ on the same universe surface
(channels 1-20). See `Docs/epics/epic-10-fx-library-and-orchestrator.md`.
"""
