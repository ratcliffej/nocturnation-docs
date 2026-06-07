#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
#
# Generates qlc-fixtures/nocturnation-lume-group-v2.qxf - a single QLC+
# fixture file that maps the entire NocturNation universe (Broadcast +
# 6 Groups, 7 x 40 = 280 channels) into ONE fixture definition. Each
# group's channels carry a group-prefixed name ("Broadcast Master
# Intensity", "Group 1 Master Intensity", ...) so the LD sees distinct
# entries per group in QLC+'s Fixtures / Scenes panels.
#
# Why one fixture instead of seven separate patches:
#   - Single patch operation (drop the fixture at universe address 1,
#     done) - no risk of address misalignment between Broadcast at 1
#     and Group N at 41 + 40*(N-1).
#   - The fixture file itself enumerates every group; the LD doesn't
#     have to remember "I need 7 instances at these specific addresses".
#   - Less LD UI clutter than 7 fixtures sharing the same name.
#
# Trade-off vs the seven-instance pattern:
#   - Locks the layout to "Broadcast then Group 1..6 at 40-channel
#     spacing starting at universe address 1". Re-patching the fixture
#     at a different base address shifts everything together. With the
#     seven-instance pattern the LD could (in theory) re-address one
#     group without touching the others; in practice nobody does that.
#
# Layout per group (offset within the 40-channel block):
#   0   Master Intensity
#   1   Strobe Rate
#   2-4 Pulse R / G / B
#   5   Pulse Trigger
#   6-9 Pulse Attack / Sustain / Release / Probability  (enum named ranges)
#   10-12 Wash A R / G / B
#   13-15 Wash B R / G / B
#   16  Wash Cycle              (100 ms units; 0 = hold A)
#   17  Wash Intensity
#   18  Wash Attack             (100 ms units)
#   19  Wash Release            (100 ms units)
#   20  Wash TTL Lo             (u16 LE seconds; pair with TTL Hi)
#   21  Wash TTL Hi
#   22  Wash Pulse Response     (>=128 enables PULSE on wash)
#   23-39 Reserved              (firmware ignores; future expansion)
#
# Re-run:  python3 qlc-fixtures/generate-v2-fixture.py
# Then commit the regenerated nocturnation-lume-group-v2.qxf alongside
# any change to this script.

from pathlib import Path

GROUPS = [
    "Broadcast",
    "Group 1",
    "Group 2",
    "Group 3",
    "Group 4",
    "Group 5",
    "Group 6",
]

# pixmob::Time enum (8 values, 32-step buckets across the 0..255 slider).
TIME_RANGES = [
    (0,   31,  "T_0_MS (instant)"),
    (32,  63,  "T_32_MS"),
    (64,  95,  "T_96_MS"),
    (96,  127, "T_192_MS"),
    (128, 159, "T_480_MS"),
    (160, 191, "T_960_MS"),
    (192, 223, "T_2400_MS"),
    (224, 255, "T_3840_MS"),
]

# pixmob::Chance enum, INVERTED so high slider = high chance (matches LD
# muscle memory). The firmware mapper does the inversion before packing.
CHANCE_RANGES = [
    (0,   31,  "4% (rare sparkle)"),
    (32,  63,  "10%"),
    (64,  95,  "16%"),
    (96,  127, "32%"),
    (128, 159, "50%"),
    (160, 191, "67%"),
    (192, 223, "88%"),
    (224, 255, "100% (every Lume fires)"),
]


def time_capabilities():
    return "\n".join(
        f'  <Capability Min="{lo}" Max="{hi}">{label}</Capability>'
        for lo, hi, label in TIME_RANGES
    )


def chance_capabilities():
    return "\n".join(
        f'  <Capability Min="{lo}" Max="{hi}">{label}</Capability>'
        for lo, hi, label in CHANCE_RANGES
    )


def channel_block(group: str) -> str:
    """Return the 23 active channel definitions for one group."""
    p = group  # prefix
    return f"""
 <!-- ============================================================
      {group.upper()}
      ============================================================ -->
 <Channel Name="{p} Master Intensity" Preset="IntensityDimmer"/>

 <Channel Name="{p} Strobe Rate">
  <Group Byte="0">Shutter</Group>
  <Capability Min="0" Max="0" Preset="ShutterOpen">Off</Capability>
  <Capability Min="1" Max="255" Preset="StrobeSlowToFast">Slow &gt; Fast (max 4 Hz)</Capability>
 </Channel>

 <Channel Name="{p} Pulse R" Preset="IntensityRed"/>
 <Channel Name="{p} Pulse G" Preset="IntensityGreen"/>
 <Channel Name="{p} Pulse B" Preset="IntensityBlue"/>

 <Channel Name="{p} Pulse Trigger">
  <Group Byte="0">Effect</Group>
  <Capability Min="0" Max="127">Idle</Capability>
  <Capability Min="128" Max="255">Fire (rising edge)</Capability>
 </Channel>

 <Channel Name="{p} Pulse Attack">
  <Group Byte="0">Effect</Group>
{time_capabilities()}
 </Channel>

 <Channel Name="{p} Pulse Sustain">
  <Group Byte="0">Effect</Group>
{time_capabilities()}
 </Channel>

 <Channel Name="{p} Pulse Release">
  <Group Byte="0">Effect</Group>
{time_capabilities()}
 </Channel>

 <Channel Name="{p} Pulse Probability">
  <Group Byte="0">Effect</Group>
{chance_capabilities()}
 </Channel>

 <Channel Name="{p} Wash A R" Preset="IntensityRed"/>
 <Channel Name="{p} Wash A G" Preset="IntensityGreen"/>
 <Channel Name="{p} Wash A B" Preset="IntensityBlue"/>

 <Channel Name="{p} Wash B R" Preset="IntensityRed"/>
 <Channel Name="{p} Wash B G" Preset="IntensityGreen"/>
 <Channel Name="{p} Wash B B" Preset="IntensityBlue"/>

 <Channel Name="{p} Wash Cycle">
  <Group Byte="0">Effect</Group>
  <Capability Min="0" Max="0">Hold anchor A (no cycle)</Capability>
  <Capability Min="1" Max="255">100 ms units (0.1 s &gt; 25.5 s; A&lt;&gt;B&lt;&gt;A)</Capability>
 </Channel>

 <Channel Name="{p} Wash Intensity">
  <Group Byte="0">Intensity</Group>
  <Capability Min="0" Max="255">Wash brightness (independent of Master)</Capability>
 </Channel>

 <Channel Name="{p} Wash Attack">
  <Group Byte="0">Effect</Group>
  <Capability Min="0" Max="255">100 ms units (fade-in time)</Capability>
 </Channel>

 <Channel Name="{p} Wash Release">
  <Group Byte="0">Effect</Group>
  <Capability Min="0" Max="255">100 ms units (default fade-out)</Capability>
 </Channel>

 <Channel Name="{p} Wash TTL Lo">
  <Group Byte="0">Effect</Group>
  <Capability Min="0" Max="255">u16 low byte (seconds; pair with TTL Hi)</Capability>
 </Channel>

 <Channel Name="{p} Wash TTL Hi">
  <Group Byte="1">Effect</Group>
  <Capability Min="0" Max="255">u16 high byte (0 + Lo = infinite)</Capability>
 </Channel>

 <Channel Name="{p} Wash Pulse Response">
  <Group Byte="0">Effect</Group>
  <Capability Min="0" Max="127">Suppress PULSE during wash</Capability>
  <Capability Min="128" Max="255">Allow PULSE overlay on wash</Capability>
 </Channel>
"""


# A single "Reserved" definition is referenced for every reserved slot
# across every group. Distinct named references would clutter QLC+ for
# no LD-facing benefit.
RESERVED = """
 <!-- ============================================================
      RESERVED PADDING (shared definition, used for every offset
      24-40 across every group block).
      ============================================================ -->
 <Channel Name="Reserved">
  <Group Byte="0">Nothing</Group>
  <Capability Min="0" Max="255">Reserved for future expansion</Capability>
 </Channel>
"""


# Per-group channel ordering: 23 active names + 17 reserved entries.
# When emitted in the Mode this list expands the group's block.
ACTIVE_CHANNEL_NAMES = [
    "Master Intensity",
    "Strobe Rate",
    "Pulse R",
    "Pulse G",
    "Pulse B",
    "Pulse Trigger",
    "Pulse Attack",
    "Pulse Sustain",
    "Pulse Release",
    "Pulse Probability",
    "Wash A R",
    "Wash A G",
    "Wash A B",
    "Wash B R",
    "Wash B G",
    "Wash B B",
    "Wash Cycle",
    "Wash Intensity",
    "Wash Attack",
    "Wash Release",
    "Wash TTL Lo",
    "Wash TTL Hi",
    "Wash Pulse Response",
]
RESERVED_PER_BLOCK = 17  # channels 24..40 (zero-indexed offsets 23..39)
BLOCK_SIZE = 40
assert len(ACTIVE_CHANNEL_NAMES) + RESERVED_PER_BLOCK == BLOCK_SIZE


def mode_entries() -> str:
    """Return all 280 <Channel Number="N">Name</Channel> lines in order."""
    out = []
    for group_idx, group in enumerate(GROUPS):
        base = group_idx * BLOCK_SIZE
        for offset, name in enumerate(ACTIVE_CHANNEL_NAMES):
            out.append(
                f'  <Channel Number="{base + offset}">{group} {name}</Channel>'
            )
        for k in range(RESERVED_PER_BLOCK):
            out.append(
                f'  <Channel Number="{base + len(ACTIVE_CHANNEL_NAMES) + k}">Reserved</Channel>'
            )
    return "\n".join(out)


def heads() -> str:
    """Three Heads per group (Pulse RGB, Wash A RGB, Wash B RGB) so QLC+'s
    Color Tool recognises the triplets. Indices are 0-based universe
    offsets within the fixture.
    """
    out = []
    for group_idx in range(len(GROUPS)):
        base = group_idx * BLOCK_SIZE
        # Pulse RGB: offsets 2,3,4
        out.append(
            f"  <Head><Channel>{base + 2}</Channel>"
            f"<Channel>{base + 3}</Channel>"
            f"<Channel>{base + 4}</Channel></Head>"
        )
        # Wash A RGB: offsets 10,11,12
        out.append(
            f"  <Head><Channel>{base + 10}</Channel>"
            f"<Channel>{base + 11}</Channel>"
            f"<Channel>{base + 12}</Channel></Head>"
        )
        # Wash B RGB: offsets 13,14,15
        out.append(
            f"  <Head><Channel>{base + 13}</Channel>"
            f"<Channel>{base + 14}</Channel>"
            f"<Channel>{base + 15}</Channel></Head>"
        )
    return "\n".join(out)


HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE FixtureDefinition>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Creator>
  <Name>NocturNation</Name>
  <Version>0.3.0</Version>
  <Author>NocturNation contributors</Author>
 </Creator>
 <Manufacturer>NocturNation</Manufacturer>
 <Model>Lume Universe v2 (DMX bridge)</Model>
 <Type>Color Changer</Type>

 <!--
   Epic 7 B7 - single-fixture universe layout.

   ONE fixture instance, 280 channels total. Patch at universe
   address 1. Channels are grouped into seven 40-channel blocks:

     1-40    Broadcast  (target_group=0; reaches every Lume)
     41-80   Group 1
     81-120  Group 2
     121-160 Group 3
     161-200 Group 4
     201-240 Group 5
     241-280 Group 6

   Each block exposes the same 23 active channels (offsets 1-23
   within the block) plus 17 reserved slots (offsets 24-40) for
   future expansion. The firmware ignores reserved channels.

   Channel names are prefixed with the group name ("Broadcast Master
   Intensity", "Group 1 Master Intensity", ...) so they appear as
   distinct entries in QLC+'s Fixtures / Scenes / Simple Desk views.

   Total universe consumption: 280 of 512 channels (232 spare).

   Generated by qlc-fixtures/generate-v2-fixture.py. Edit the
   generator, re-run, and commit both.
 -->
"""

FOOTER = """
 <Physical>
  <Bulb Type="LED" Lumens="0" ColourTemperature="0"/>
  <Dimensions Weight="0" Width="0" Height="0" Depth="0"/>
  <Lens Name="Other" DegreesMin="0" DegreesMax="0"/>
  <Focus Type="Fixed" PanMax="0" TiltMax="0"/>
  <Technical PowerConsumption="0" DmxConnector="Other"/>
 </Physical>
</FixtureDefinition>
"""


def main():
    parts = [HEADER]
    for group in GROUPS:
        parts.append(channel_block(group))
    parts.append(RESERVED)
    parts.append("""
 <!-- ============================================================
      MODE: Universe (280ch). Patch this fixture ONCE at universe
      address 1.
      ============================================================ -->
 <Mode Name="Universe (280ch)">
""")
    parts.append(mode_entries())
    parts.append("\n")
    parts.append(heads())
    parts.append("\n </Mode>\n")
    parts.append(FOOTER)

    output_path = Path(__file__).parent / "nocturnation-lume-group-v2.qxf"
    output_path.write_text("".join(parts))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
