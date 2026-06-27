#!/usr/bin/env python3
"""png_to_rgb565.py - convert an image to a flat RGB565 .raw blob for the
Tildagon Lume background-image layer (Epic 13 Phase 2A).

The Tildagon's MicroPython Ctx binding exposes ``ctx.texture(buf, fmt, w,
h, stride)`` accepting the ``ctx.FORMAT_RGB565`` (little-endian) layout.
A pre-rendered binary blob means zero decode cost at runtime; we just
``open(path, 'rb').read()`` into a bytes buffer and hand it straight to
the texture call.

Typical authoring step:

    ./png_to_rgb565.py stage-d.png --out images/dirid_d0.raw
    # default size matches the Tildagon's display (240x240); override
    # with --size to author a smaller image:
    ./png_to_rgb565.py icon.png --size 168x168 --out images/dirid_a1.raw

The .raw file contains exactly ``width * height * 2`` bytes of pixel
data (no header, no palette). Pair it with a ``.meta.txt`` sidecar so
the source image + dimensions are recoverable later; the sidecar is for
human eyes only and is not loaded by the firmware.

Requires Pillow. Install once into the laptop's venv:

    pip install pillow
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print(
        "error: Pillow not installed. Run: pip install pillow",
        file=sys.stderr,
    )
    sys.exit(2)


# Tildagon's circular display is roughly 240 x 240 pixels; the bezel
# masks the corners but the framebuffer addresses the full square.
DEFAULT_SIZE = (240, 240)


def encode_rgb565_le(img: "Image.Image") -> bytes:
    """Pack an RGB image to little-endian RGB565 bytes.

    RGB565 layout per pixel (LE on the wire = low byte first):

        bits 15..11: R (5 bits, top 5 of source R)
        bits 10..5 : G (6 bits, top 6 of source G)
        bits 4..0  : B (5 bits, top 5 of source B)

    Returns a ``bytes`` object of length ``w * h * 2``. Caller is
    responsible for any rounding / dithering decisions before calling.
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    pixels = img.tobytes()  # 'RGB' = 3 bytes per pixel, row-major
    out = bytearray(w * h * 2)
    for i in range(w * h):
        r = pixels[i * 3]
        g = pixels[i * 3 + 1]
        b = pixels[i * 3 + 2]
        # Top-bit alignment - shifts away the precision we lose.
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[i * 2]     = v & 0xFF        # low byte first (LE)
        out[i * 2 + 1] = (v >> 8) & 0xFF
    return bytes(out)


def parse_size(spec: str) -> tuple[int, int]:
    """Parse ``"240x240"`` style size; sanity-check bounds."""
    parts = spec.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(
            f"size must be WxH (e.g. 240x240), got {spec!r}"
        )
    try:
        w, h = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"size must be integer WxH, got {spec!r}"
        ) from exc
    if not (1 <= w <= 1024 and 1 <= h <= 1024):
        raise argparse.ArgumentTypeError(
            f"size {w}x{h} out of bounds (1..1024 each)"
        )
    return w, h


def write_meta_sidecar(out_path: Path, source: Path, w: int, h: int, data: bytes) -> None:
    """Drop a human-readable sidecar next to the .raw blob.

    Records source filename, dimensions, byte count, and SHA-256 so the
    .raw provenance is traceable when bench-debugging "why does the
    Tildagon show the wrong logo".
    """
    sidecar = out_path.with_suffix(out_path.suffix + ".meta.txt")
    digest = hashlib.sha256(data).hexdigest()
    sidecar.write_text(
        "source: {src}\n"
        "size:   {w}x{h}\n"
        "format: rgb565-le\n"
        "bytes:  {n}\n"
        "sha256: {sha}\n".format(
            src=source.name, w=w, h=h, n=len(data), sha=digest
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert an image to RGB565-LE binary for Tildagon Lume backgrounds.",
    )
    parser.add_argument("input", type=Path, help="Source image (PNG / JPEG / any Pillow-loadable)")
    parser.add_argument(
        "--out", "-o", type=Path,
        help="Output path. Default: <input>.raw next to the source.",
    )
    parser.add_argument(
        "--size", type=parse_size, default=DEFAULT_SIZE,
        metavar="WxH",
        help="Target dimensions. Default: 240x240 (Tildagon display).",
    )
    parser.add_argument(
        "--fit",
        choices=("cover", "contain", "stretch"),
        default="cover",
        help=(
            "How to fit a non-matching source: 'cover' crops to fill "
            "(default - good for logos), 'contain' letterboxes, "
            "'stretch' distorts to fit."
        ),
    )
    parser.add_argument(
        "--no-meta",
        action="store_true",
        help="Skip writing the .meta.txt sidecar.",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"error: input not found: {args.input}", file=sys.stderr)
        return 2

    out = args.out if args.out is not None else args.input.with_suffix(".raw")
    out.parent.mkdir(parents=True, exist_ok=True)

    target_w, target_h = args.size

    try:
        img = Image.open(args.input)
    except Exception as exc:  # noqa: BLE001 - Pillow raises a zoo of types
        print(f"error: could not open {args.input}: {exc}", file=sys.stderr)
        return 2

    # Fit policy. Both 'cover' and 'contain' preserve aspect ratio;
    # 'stretch' is the simplest and matches what Pillow's resize() does.
    if args.fit == "stretch":
        img = img.resize((target_w, target_h), Image.LANCZOS)
    else:
        src_w, src_h = img.size
        src_aspect = src_w / src_h
        dst_aspect = target_w / target_h
        if args.fit == "cover":
            # Scale so the smaller dimension matches; crop the overflow.
            if src_aspect > dst_aspect:
                # Source is wider -> scale by height, crop width.
                new_h = target_h
                new_w = int(round(src_aspect * new_h))
            else:
                new_w = target_w
                new_h = int(round(new_w / src_aspect))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - target_w) // 2
            top  = (new_h - target_h) // 2
            img = img.crop((left, top, left + target_w, top + target_h))
        else:  # contain
            if src_aspect > dst_aspect:
                new_w = target_w
                new_h = int(round(new_w / src_aspect))
            else:
                new_h = target_h
                new_w = int(round(new_h * src_aspect))
            img = img.resize((new_w, new_h), Image.LANCZOS)
            # Letterbox onto a black canvas.
            canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
            canvas.paste(img, ((target_w - new_w) // 2, (target_h - new_h) // 2))
            img = canvas

    data = encode_rgb565_le(img)
    expected = target_w * target_h * 2
    if len(data) != expected:
        print(
            f"internal error: encoded {len(data)} bytes, expected {expected}",
            file=sys.stderr,
        )
        return 3

    out.write_bytes(data)
    print(f"wrote {out} ({len(data)} bytes, {target_w}x{target_h} rgb565-le)")

    if not args.no_meta:
        write_meta_sidecar(out, args.input, target_w, target_h, data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
