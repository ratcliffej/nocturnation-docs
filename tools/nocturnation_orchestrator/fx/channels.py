"""DMX channel constants for the StickC LD-facing surface.

Mirrors the per-block layout in
`StickC/src/dal/drivers/dmx_channel_mapper.h`. Each block is 40
universe channels wide; the first 20 are active, the remaining 20 are
reserved.

Block index in the universe:
    Block 0 (broadcast)  -> universe channels   1.. 40
    Block 1 (group 1)    -> universe channels  41.. 80
    Block 2 (group 2)    -> universe channels  81..120
    ...
    Block 9 (group 9)    -> universe channels 361..400

Constants below give the 1-indexed *universe* channel for block 0
(broadcast). Use `block_channel(block, channel)` to compute the
universe channel for groups 1..9.
"""

# Per-block layout (block 0 / broadcast).
CH_MASTER       = 1   # Master Intensity (RGB scalar across all output).
CH_STROBE       = 2   # 1..255 -> 0..4 Hz pulse cadence; 0 = off.
CH_PULSE_R      = 3
CH_PULSE_G      = 4
CH_PULSE_B      = 5
CH_PULSE_TRIG   = 6   # Rising edge 0->>=128 fires one pulse; must
                      # drop below 128 to re-arm.
CH_PULSE_ATK    = 7   # Pulse Attack  (Time enum slider)
CH_PULSE_SUS    = 8   # Pulse Sustain (Time enum slider)
CH_PULSE_REL    = 9   # Pulse Release (Time enum slider)
CH_PULSE_PROB   = 10  # Probability (inverted - high slider = high chance)
CH_WASH_A_R     = 11
CH_WASH_A_G     = 12
CH_WASH_A_B     = 13
CH_WASH_B_R     = 14
CH_WASH_B_G     = 15
CH_WASH_B_B     = 16
CH_WASH_CYCLE   = 17  # 100 ms units; 0 = hold A, 1..255 = 100ms..25.5s
CH_WASH_INT     = 18  # Wash Intensity
CH_WASH_ATK     = 19  # Wash Attack  (100 ms units)
CH_WASH_REL     = 20  # Wash Release (100 ms units)
CH_WASH_TTL_LO  = 21  # Wash TTL low byte  (u16 LE seconds; 0 = infinite)
CH_WASH_TTL_HI  = 22  # Wash TTL high byte
CH_WASH_PULSE_RESPONSE = 23  # >=128 -> Lume allows PULSE overlay on wash;
                             # <128 -> Lume drops PULSE while wash is in
                             # ATTACK / HOLD. Wash FX should write 255 if
                             # they expect accent pulses to be visible.

# Raw-RGB path (EMF artist-stage request 2026-06-23). When CH_RAW_ENABLE
# is >= 128, the StickC mapper treats the block as a dumb 3-channel
# RGB fixture: emits a static LIGHT_WASH built from CH_RAW_R/G/B
# (master-scaled) and SUPPRESSES the FX engine's pulse / wash / strobe
# output for the block until enable drops back below 128. Lets an LD
# bypass the FX engine entirely for plain-light control of Lumes.
# Orchestrator FX library doesn't write these - they're for direct DMX
# from the LD's console.
CH_RAW_R       = 24
CH_RAW_G       = 25
CH_RAW_B       = 26
CH_RAW_ENABLE  = 27   # >=128 -> raw mode active; <128 -> FX path

# Block geometry.
BLOCK_WIDTH = 40
ACTIVE_CHANNELS_PER_BLOCK = 27
NUM_BLOCKS = 10       # Broadcast + groups 1..9.

# Pulse-trigger threshold; matches dmx_channel_mapper::kTriggerHi.
TRIGGER_HI = 255      # Write 255 to fire; orchestrator drops to 0 to re-arm.
TRIGGER_LO = 0


def block_channel(block, channel):
    """Translate a block-local channel to its universe channel.

    Args:
        block (int): 0 = broadcast, 1..9 = group block index.
        channel (int): 1..ACTIVE_CHANNELS_PER_BLOCK (one of the CH_*
            constants).
    """
    if not (0 <= block < NUM_BLOCKS):
        raise ValueError("block out of range: %d" % block)
    if not (1 <= channel <= ACTIVE_CHANNELS_PER_BLOCK):
        raise ValueError("channel out of range for block: %d" % channel)
    return block * BLOCK_WIDTH + channel


def clamp_group(value):
    """Clamp a `group` param value to a valid block index (0..9).

    0 = broadcast (every Lume regardless of configured group), 1..9 =
    that group only. Used by every single-target FX to coerce a
    possibly-malformed cue-file value into a safe block index.
    """
    if value is None or value < 0:
        return 0
    if value > 9:
        return 9
    return value


def percent_to_dmx(value):
    """Convert a percent value (0..100) declared by an FX `("percent",
    ...)` PARAMS entry into the 0..255 DMX scalar the wire surface
    expects.

    Bug: FX files were declaring probability params as "percent" but
    writing the raw int to DMX, so cue-file authors writing '50'
    expecting 50% were getting DMX 50, which the StickC's mapper
    buckets to CHANCE_10 (10%). This helper makes the percent <-> DMX
    conversion explicit so the FX UX matches its docs.

    Value clamped to 0..100; out-of-range silently coerces (>100 -> 100,
    <0 -> 0) rather than raising, so a malformed cue line produces a
    sensible default instead of crashing the runtime.

    Quantisation note: the StickC mapper buckets DMX 0..255 into 8
    Chance enum slots (CHANCE_4 .. CHANCE_100). 0% lands on CHANCE_4
    (4% effective floor), 50% on CHANCE_50, 87.5%+ on CHANCE_100.
    Bucket boundaries are inherent to the PixMob wire format.
    """
    if value is None or value <= 0:
        return 0
    if value >= 100:
        return 255
    return int(value * 255 / 100 + 0.5)
