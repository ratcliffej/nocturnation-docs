#!/usr/bin/env python3
"""make_tildagon_template.py - generate a 240x240 PNG designer template
showing the Tildagon's circular-display edge + safe zone.

Designers import the output PNG as a guide layer in their image editor,
build their logo on top, then export their finished work at 240x240
and run ``png_to_rgb565.py`` to produce the deployable .raw blob.

Output: ``tildagon-display-template.png`` next to this script (or
``--out PATH`` for a custom destination).

Two concentric guide circles:

* **Outer circle (red, radius 120)** - the actual visible-pixel edge.
  Anything outside this circle is wasted: written to the framebuffer
  but masked by the physical bezel. Designers should keep solid
  background fills inside this boundary.

* **Inner circle (amber, radius 110)** - the recommended safe zone.
  Logo glyphs, text, and other "must read" content should sit inside
  this margin. The 10-pixel gap accounts for anti-aliasing softness
  near the bezel plus a small alignment tolerance during assembly.

Plus a crosshair at the centre so designers can verify their content
is centred without manual measuring.

Requires Pillow (same as ``png_to_rgb565.py``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print(
        "error: Pillow not installed. Run: pip install pillow",
        file=sys.stderr,
    )
    sys.exit(2)


CANVAS_SIZE       = 240
CENTRE            = (CANVAS_SIZE // 2, CANVAS_SIZE // 2)
PANEL_EDGE_RADIUS = 120   # full visible disc - matches the GC9A01 panel
SAFE_ZONE_RADIUS  = 110   # recommended safe area for key content
CROSSHAIR_LEN     = 6     # px ticks each side of the centre point

# Colours: semi-transparent so designers can see their content
# through the guides. RGBA on a transparent background.
COLOUR_BACKGROUND  = (255, 255, 255, 0)      # fully transparent
COLOUR_PANEL_EDGE  = (255,   0,   0, 200)    # red, ~80 % opaque
COLOUR_SAFE_ZONE   = (255, 180,   0, 160)    # amber, ~60 % opaque
COLOUR_CROSSHAIR   = (  0,   0,   0, 180)    # near-black


def _draw_circle_outline(draw: "ImageDraw.ImageDraw",
                         centre: tuple[int, int],
                         radius: int,
                         colour: tuple[int, int, int, int],
                         width: int = 1) -> None:
    cx, cy = centre
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)
    draw.ellipse(bbox, outline=colour, width=width)


def _draw_crosshair(draw: "ImageDraw.ImageDraw",
                    centre: tuple[int, int],
                    half_len: int,
                    colour: tuple[int, int, int, int]) -> None:
    cx, cy = centre
    draw.line((cx - half_len, cy,         cx + half_len, cy        ), fill=colour, width=1)
    draw.line((cx,            cy - half_len, cx,         cy + half_len), fill=colour, width=1)


def _draw_label(draw: "ImageDraw.ImageDraw",
                xy: tuple[int, int],
                text: str,
                colour: tuple[int, int, int, int]) -> None:
    """Best-effort label using whatever font Pillow can find."""
    try:
        font = ImageFont.load_default()
    except OSError:
        font = None
    draw.text(xy, text, fill=colour, font=font)


def render(out_path: Path) -> None:
    img  = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), COLOUR_BACKGROUND)
    draw = ImageDraw.Draw(img)

    # Outer panel-edge circle.
    _draw_circle_outline(draw, CENTRE, PANEL_EDGE_RADIUS,
                         COLOUR_PANEL_EDGE, width=1)
    # Inner safe-zone circle.
    _draw_circle_outline(draw, CENTRE, SAFE_ZONE_RADIUS,
                         COLOUR_SAFE_ZONE, width=1)
    # Crosshair at centre.
    _draw_crosshair(draw, CENTRE, CROSSHAIR_LEN, COLOUR_CROSSHAIR)

    # Small labels positioned to not overlap the centre or the
    # circles. Labels are 1-character ish so we don't need a fancy
    # font; the default Pillow bitmap font is fine.
    _draw_label(draw, (8, 4),                       "240x240",       COLOUR_CROSSHAIR)
    _draw_label(draw, (CENTRE[0] + 4, CENTRE[1] - PANEL_EDGE_RADIUS + 2),
                "r=120 edge",                                         COLOUR_PANEL_EDGE)
    _draw_label(draw, (CENTRE[0] + 4, CENTRE[1] - SAFE_ZONE_RADIUS + 2),
                "r=110 safe",                                         COLOUR_SAFE_ZONE)

    img.save(out_path, "PNG")
    print(f"wrote {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate a 240x240 Tildagon-display designer template PNG.",
    )
    parser.add_argument(
        "--out", "-o", type=Path,
        default=Path(__file__).resolve().parent / "tildagon-display-template.png",
        help=(
            "Output PNG path. Default: tildagon-display-template.png "
            "next to this script."
        ),
    )
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    render(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
