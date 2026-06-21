#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
"""Send a one-shot TEXT_DISPLAY frame to the NocturNation fleet.

Bench utility for Epic 13 B2. Emits a single TEXT_DISPLAY ESP-NOW
frame through either:

  * direct USB (default) - opens the StickC port, wraps in Enttec
    label 0x10, writes. Stick must be in DMX Bridge mode for the
    passthrough to fire.

  * Art-Net (--output artnet) - sends an OpVendorEspNow UDP packet
    to 127.0.0.1:6454 (or --artnet-host/--artnet-port). The shim
    (artnet-to-enttec-pro.py) decodes and forwards to the Stick.

Either way the Stick unwraps and broadcasts; any Lume with
Capability::DisplayText receives + renders.

Examples:

  # Sticky header + body via direct USB
  ./send_text.py --header "Coldplay" --body "Adventure of a Lifetime"

  # Body-only, 5-second auto-clear, orange
  ./send_text.py --body "Turn your magic on" --ttl-ms 5000 --rgb 255,128,0

  # Same content via the QLC+ shim path
  ./send_text.py --output artnet --header "Coldplay" --body "Adventure of a Lifetime"

  # Clear screen (text layer only by default)
  ./send_text.py --clear

  # Clear both layers
  ./send_text.py --clear --clear-bitmap
"""

from __future__ import annotations

import argparse
import sys
import time

# Add the parent tools/ directory to the path so we can import the
# shared nocturnation_dmx package and the orchestrator's output
# dispatchers. Script lives in tools/scripts/.
import os
_TOOLS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _TOOLS_DIR)

from nocturnation_dmx import UsbWriter, espnow_frame
from nocturnation_dmx.port_picker import find_candidate_ports_with_info
from nocturnation_orchestrator.output.artnet import ArtnetDispatcher


def _pick_first_stickc_port():
    candidates = find_candidate_ports_with_info()
    if not candidates:
        raise SystemExit(
            "no StickC-shaped USB serial port found; plug a Plus2 / S3 in"
        )
    device, desc = candidates[0]
    print(f"[send_text] using {device} ({desc})", file=sys.stderr)
    return device


def _parse_rgb(s: str) -> tuple[int, int, int]:
    parts = s.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"--rgb must be R,G,B (got {s!r})")
    try:
        r, g, b = (int(p) for p in parts)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))
    for name, v in (("r", r), ("g", g), ("b", b)):
        if not (0 <= v <= 255):
            raise argparse.ArgumentTypeError(f"{name}={v} out of range 0..255")
    return r, g, b


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output", choices=("usb", "artnet"), default="usb",
                   help="transport: direct USB (default) or Art-Net to the shim")
    p.add_argument("--port", help="(usb mode) USB serial device (default: auto-detect)")
    p.add_argument("--baud", type=int, default=None,
                   help="(usb mode) override baud rate (Plus2 default 460800; S3 ignores)")
    p.add_argument("--artnet-host", default="127.0.0.1",
                   help="(artnet mode) shim host (default 127.0.0.1)")
    p.add_argument("--artnet-port", type=int, default=6454,
                   help="(artnet mode) shim UDP port (default 6454)")

    g = p.add_argument_group("text content")
    # nargs='?' so `--header` with no value means empty (body-only mode).
    # Same for --body. Argparse otherwise treats the next token starting
    # with `-` as a value-missing error.
    g.add_argument("--header", nargs="?", default="", const="",
                   help="header line (≤64 UTF-8 bytes; bare --header = empty)")
    g.add_argument("--body",   nargs="?", default="", const="",
                   help="body text  (≤128 UTF-8 bytes; bare --body = empty)")
    g.add_argument("--rgb",    type=_parse_rgb, default=(255, 255, 255),
                   help="text colour R,G,B (default 255,255,255)")
    g.add_argument("--ttl-ms", type=int, default=0,
                   help="auto-clear after N ms (0 = sticky)")
    g.add_argument("--group",  type=int, default=0,
                   help="target Lume group (0 = all)")

    c = p.add_argument_group("clear-screen mode (instead of sending text)")
    c.add_argument("--clear",        action="store_true",
                   help="emit CLEAR_SCREEN instead of TEXT_DISPLAY")
    c.add_argument("--clear-text",   action="store_true", default=None,
                   help="(with --clear) clear text layer (default: yes)")
    c.add_argument("--clear-bitmap", action="store_true", default=None,
                   help="(with --clear) clear bitmap layer (default: no)")

    p.add_argument("--source-id", type=int, default=espnow_frame.BROADCAST_SOURCE_ID,
                   help="ESP-NOW source_id (default 0xFF = broadcast)")
    p.add_argument("--sequence",  type=int, default=None,
                   help="ESP-NOW sequence_number (default: low byte of time())")
    p.add_argument("--repeat",   type=int, default=1,
                   help="send the frame N times with a small inter-frame gap")

    args = p.parse_args(argv)

    sequence = args.sequence
    if sequence is None:
        sequence = int(time.time()) & 0xFF
        if sequence == 0:
            sequence = 1  # 0 = dedup off; we want unique sequence per send

    if args.clear:
        clear_text   = True  if args.clear_text   is None else bool(args.clear_text)
        clear_bitmap = False if args.clear_bitmap is None else bool(args.clear_bitmap)
        frame = espnow_frame.encode_clear_screen(
            source_id=args.source_id,
            sequence=sequence,
            target_group=args.group,
            clear_text=clear_text,
            clear_bitmap=clear_bitmap,
        )
        kind = (
            f"CLEAR_SCREEN(text={int(clear_text)} bitmap={int(clear_bitmap)})"
        )
    else:
        r, g, b = args.rgb
        frame = espnow_frame.encode_text_display(
            source_id=args.source_id,
            sequence=sequence,
            target_group=args.group,
            r=r, g=g, b=b,
            ttl_ms=args.ttl_ms,
            header=args.header,
            body=args.body,
        )
        kind = (
            f"TEXT_DISPLAY(group={args.group} rgb={r},{g},{b} "
            f"ttl={args.ttl_ms} hdr={args.header!r} body={args.body!r})"
        )

    if args.output == "artnet":
        dispatcher = ArtnetDispatcher.open(
            host=args.artnet_host, port=args.artnet_port
        )
        target = f"art-net {args.artnet_host}:{args.artnet_port}"
        try:
            for i in range(max(1, args.repeat)):
                ok = dispatcher.send_espnow_frame(frame)
                status = "sent" if ok else "FAILED"
                print(
                    f"[send_text] {status} #{i+1}/{args.repeat} via {target}: "
                    f"{kind} ({len(frame)} bytes)",
                    file=sys.stderr,
                )
                if args.repeat > 1 and i + 1 < args.repeat:
                    time.sleep(0.1)
        finally:
            dispatcher.close()
        return 0

    # --output usb (direct)
    port = args.port or _pick_first_stickc_port()
    writer = UsbWriter(port) if args.baud is None else UsbWriter(port, baud=args.baud)
    if not writer.is_open:
        raise SystemExit(f"could not open {port} (already in use?)")
    try:
        for i in range(max(1, args.repeat)):
            writer.write_espnow_frame(frame)
            print(
                f"[send_text] sent #{i+1}/{args.repeat} via usb {port}: "
                f"{kind} ({len(frame)} bytes)",
                file=sys.stderr,
            )
            if args.repeat > 1 and i + 1 < args.repeat:
                time.sleep(0.1)
    finally:
        writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
