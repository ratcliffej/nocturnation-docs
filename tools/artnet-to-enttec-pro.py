#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
#
# artnet-to-enttec-pro.py
#
# NocturNation Epic 7 shim. Listens for QLC+'s Art-Net Op Output packets on
# UDP 6454, wraps the DMX-512 payload in Enttec DMX USB Pro framing, and
# writes it to a USB-CDC serial port connected to a NocturNation StickC or
# Tildagon running in DMX Bridge mode.
#
#   QLC+  -->  Art-Net UDP  -->  this shim  -->  Enttec Pro USB-CDC  -->  device
#
# Art-Net packet decode references mich181189/Tildagon-ArtNet (BSD-3-Clause).
# See Epic 7 working copy in nocturnation-docs/epics/epic-07-dmx-qlc.md for
# the full architecture rationale.

import argparse
import binascii
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Optional

import serial
import serial.tools.list_ports

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# Shared library (Epic 10 B0). Both this shim and the music orchestrator
# import the framing, port-picker, and USB-write code from here so they
# stay in lockstep on Plus2 quirks, Tildagon exclusion, and baud rate.
# Behaviour-preserving refactor: every symbol the shim used to declare
# inline is now imported.
from nocturnation_dmx import (
    DMX_UNIVERSE_BYTES,
    ENTTEC_BAUD,
    UsbWriter,
    describe_port,
    device_kind_from_port,
    find_candidate_ports,
    find_candidate_ports_with_info,
    interactive_pick_port,
    open_serial,
    port_looks_like_tildagon,
    wrap_enttec_espnow,
    wrap_enttec_pro,
)


# ============================================================================
# Constants (shim-local)
# ============================================================================

VERSION = "0.1.0"

# Art-Net protocol
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_OP_OUTPUT = 0x5000
# Epic 13: NocturNation OpVendorEspNow. Carries a fully-formed
# NocturNation ESP-NOW frame; the shim unwraps and rewraps as Enttec
# label 0x10 so the Stick (in DMX bridge mode) broadcasts the inner
# frame onto the radio. Mirrors the orchestrator's _OPCODE_VENDOR_ESPNOW
# in nocturnation_orchestrator/output/artnet.py.
ARTNET_OP_VENDOR_ESPNOW = 0xFB00
ARTNET_DEFAULT_PORT = 6454

# HTP merge tuning. A source that hasn't been heard from in this
# many seconds is dropped from the merge - keeps QLC+ stopping mid-
# show from pinning its last frame on top of the orchestrator's bed.
# 500 ms is a comfortable margin over orchestrator's 250 ms poll +
# the typical Art-Net resend cadence.
MERGE_STALENESS_S = 0.5

# Channel role labels for the diagnostics view, matching Epic 7 B7
# layout (23 active channels per block, grouped by lighting concept).
# The shim shows the BROADCAST block (addresses 1-23) here; Groups
# 1-6 at addresses 41, 81, 121, 161, 201, 241 follow the same per-
# block layout but emit on their own target_group. To see what a
# Group is doing, scope the Stick's LCD diagnostics view or trust
# QLC+'s Simple Desk.
CHANNEL_ROLES = [
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
    # Channels 21-23 were Wash TTL Lo / Hi and Wash Pulse Response.
    # Dropped from the LD surface: the DMX bridge always emits
    # LIGHT_WASH frames with ttl_seconds=0 (30-min lost-WASH_END
    # failsafe handles stuck washes) and pulse_response=1 (so
    # sparkle on wash works by default). Wire protocol unchanged.
    "Reserved 21",
    "Reserved 22",
    "Reserved 23",
    # Channels 24-27 are the EMF stage-team raw-RGB path (added
    # 2026-06-23): direct LD control of each block as a dumb RGB
    # fixture, FX engine suppressed when Raw Enable >= 128.
    "Raw R",
    "Raw G",
    "Raw B",
    "Raw Enable",
]
BLOCK_WIDTH = 40       # universe channels per block
MAX_UNIVERSE_CHANNEL = 512


def channel_to_role_and_group(universe_channel):
    """Map a 1-indexed universe channel to (group_label, role_label).

    Block 0 (universe 1..40) is broadcast (target_group = 0). Block N
    (universe (40*N+1)..(40*N+40)) is group N. Within each block the
    same role layout repeats - so universe channel 44 in group 1 is
    the same role as channel 4 in broadcast (Pulse G), just addressed
    to a different fleet subset.
    """
    if not (1 <= universe_channel <= MAX_UNIVERSE_CHANNEL):
        return ("?", "out-of-range")
    zero_idx = universe_channel - 1
    block_idx = zero_idx // BLOCK_WIDTH
    offset = zero_idx % BLOCK_WIDTH
    group_label = "Broadcast" if block_idx == 0 else f"Group {block_idx}"
    if offset < len(CHANNEL_ROLES):
        role = CHANNEL_ROLES[offset]
    else:
        role = f"Reserved {offset + 1}"
    return (group_label, role)


# ============================================================================
# Art-Net decode
# ============================================================================

def decode_artnet_output(data: bytes) -> Optional[tuple[int, bytes]]:
    """Validate an Art-Net Op Output packet and extract (universe, payload).

    Returns None for any packet that isn't a well-formed Op Output - silently
    drops other Art-Net opcodes (ArtPoll, ArtPollReply, etc.) because we
    don't reply to discovery in v1.

    Frame layout (per Art-Net 4 spec):
      0..7    "Art-Net\\0"
      8..9    Opcode (little-endian)
      10..11  Protocol version (big-endian, 14+)
      12      Sequence (0 = disabled)
      13      Physical input port (informational)
      14      SubUni  (low byte of universe address)
      15      Net     (high byte of universe address)
      16..17  Length of DMX payload (big-endian!)
      18..    DMX-512 channel values, length bytes
    """
    if len(data) < 18:
        return None
    if data[:8] != ARTNET_HEADER:
        return None
    opcode = data[8] | (data[9] << 8)
    if opcode != ARTNET_OP_OUTPUT:
        return None
    sub_uni = data[14]
    net = data[15]
    universe = (net << 8) | sub_uni
    payload_len = (data[16] << 8) | data[17]
    if len(data) < 18 + payload_len:
        return None
    return universe, data[18:18 + payload_len]


def decode_artnet_vendor_espnow(data: bytes) -> Optional[bytes]:
    """Validate an OpVendorEspNow packet (Epic 13) and extract the
    inner NocturNation ESP-NOW frame.

    Returns None for any packet that isn't a well-formed OpVendorEspNow.

    Frame layout:
      0..7    "Art-Net\\0"
      8..9    Opcode 0xFB00 (little-endian on wire)
      10..11  Protocol version (big-endian, 14+)
      12..13  Inner frame length (big-endian)
      14..    Inner ESP-NOW frame, length bytes
    """
    if len(data) < 14:
        return None
    if data[:8] != ARTNET_HEADER:
        return None
    opcode = data[8] | (data[9] << 8)
    if opcode != ARTNET_OP_VENDOR_ESPNOW:
        return None
    frame_len = (data[12] << 8) | data[13]
    if frame_len == 0 or frame_len > 250:
        return None
    if len(data) < 14 + frame_len:
        return None
    return data[14:14 + frame_len]


# ============================================================================
# Enttec Pro framing + Serial port handling
# ============================================================================
# Both extracted to nocturnation_dmx (Epic 10 B0). The shim imports
# wrap_enttec_pro, find_candidate_ports*, interactive_pick_port,
# device_kind_from_port, port_looks_like_tildagon, open_serial, and
# UsbWriter from the library - one source of truth shared with the
# music orchestrator.


# ============================================================================
# Shim state
# ============================================================================

@dataclass
class ShimState:
    """All UI-relevant state, mutated by the I/O loop and read by the UI."""
    serial_port: Optional[str] = None
    serial_connected: bool = False
    bind_addr: str = "0.0.0.0"
    artnet_port: int = ARTNET_DEFAULT_PORT
    universe: int = 1
    merge_mode: str = "none"      # "none" | "htp"
    frames_received: int = 0
    frames_sent: int = 0
    frames_dropped_wrong_universe: int = 0
    errors: int = 0
    last_artnet_ts: Optional[float] = None
    last_enttec_ts: Optional[float] = None
    last_payload: Optional[bytes] = None
    fps: float = 0.0
    last_error_msg: str = ""
    _frame_times: list[float] = field(default_factory=list)
    # HTP merge tracking: live source -> (last payload bytes, last_ts).
    # Sources stale past MERGE_STALENESS_S drop out of the merge so a
    # disconnected sender doesn't pin its last frame forever.
    sources: dict = field(default_factory=dict)
    # Which universe channels the debug panel should show. None =
    # default broadcast view (channels 1..23 with no Group column).
    # Non-empty list = operator-chosen channels via --showchannels, in
    # the order given, with a Group column so the LD can see at a
    # glance which fixture block each row belongs to.
    show_channels: Optional[list] = None

    def record_frame(self, payload: bytes) -> None:
        now = time.monotonic()
        self.frames_received += 1
        self.last_artnet_ts = now
        self.last_payload = payload
        self._frame_times.append(now)
        self._recompute_fps(now)

    def tick_fps(self) -> None:
        """Recompute FPS using the current wall clock. Lets the displayed
        rate decay to 0 when no frames are arriving - without this, fps
        sticks at its last-computed value forever."""
        self._recompute_fps(time.monotonic())

    def _recompute_fps(self, now: float) -> None:
        cutoff = now - 1.0
        while self._frame_times and self._frame_times[0] < cutoff:
            self._frame_times.pop(0)
        self.fps = float(len(self._frame_times))

    def record_sent(self) -> None:
        self.last_enttec_ts = time.monotonic()
        self.frames_sent += 1

    def record_error(self, msg: str) -> None:
        self.errors += 1
        self.last_error_msg = msg


# ============================================================================
# Status UI (rich)
# ============================================================================

def _format_age(ts: Optional[float]) -> str:
    if ts is None:
        return "[dim]never[/dim]"
    age_ms = (time.monotonic() - ts) * 1000.0
    if age_ms < 1000:
        return f"{age_ms:.0f}ms ago"
    return f"{age_ms / 1000:.1f}s ago"


def build_status_layout(state: ShimState) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="info"),
        Layout(name="channels"),
    )

    layout["header"].update(Panel(
        Text.from_markup(
            f"[bold cyan]NocturNation Art-Net to Enttec Pro shim[/bold cyan]  "
            f"[dim]v{VERSION}[/dim]",
            justify="center",
        ),
        border_style="cyan",
    ))

    # Info panel: connection + counters.
    info = Table(show_header=False, expand=True, padding=(0, 1))
    info.add_column("Field", style="dim")
    info.add_column("Value")
    if state.serial_connected:
        info.add_row("Serial", f"[green]{state.serial_port}[/green]")
    elif state.serial_port:
        info.add_row("Serial", f"[yellow]waiting for {state.serial_port}[/yellow]")
    else:
        info.add_row("Serial", "[red]no device detected[/red]")
    info.add_row("Listening on", f"UDP {state.bind_addr}:{state.artnet_port}")
    info.add_row("Universe filter", str(state.universe))
    if state.merge_mode != "none":
        live = len(state.sources)
        info.add_row(
            "Merge", f"{state.merge_mode.upper()} ({live} source{'s' if live != 1 else ''} live)",
        )
    info.add_row("", "")
    info.add_row("Art-Net frames in", str(state.frames_received))
    info.add_row("Last frame in", _format_age(state.last_artnet_ts))
    info.add_row("Enttec frames out", str(state.frames_sent))
    info.add_row("Last frame out", _format_age(state.last_enttec_ts))
    info.add_row("Frame rate", f"{state.fps:.1f} fps")
    info.add_row("", "")
    other_uni_str = (
        f"[yellow]{state.frames_dropped_wrong_universe}[/yellow]"
        if state.frames_dropped_wrong_universe else "0"
    )
    info.add_row("Wrong-universe drops", other_uni_str)
    err_str = f"[red]{state.errors}[/red]" if state.errors else "0"
    info.add_row("Errors", err_str)
    if state.last_error_msg:
        info.add_row("Last error", f"[red dim]{state.last_error_msg}[/red dim]")
    layout["info"].update(Panel(info, title="Status", border_style="blue"))

    # Channels panel. Two layouts: the default "broadcast block" view
    # (channels 1..23, no Group column - the LD is patched at base
    # address 1 and watching the whole block) and the
    # --showchannels view (operator-chosen channels with a Group
    # column showing which fixture block each row addresses).
    channels = Table(show_header=True, expand=True, padding=(0, 1))
    channels.add_column("Ch", style="dim", width=4)
    if state.show_channels is not None:
        channels.add_column("Group", width=10)
    channels.add_column("Role", width=18)
    channels.add_column("Hex", width=4)
    channels.add_column("Val", width=4)
    channels.add_column("Level", ratio=1)
    payload = state.last_payload or b""

    if state.show_channels is not None:
        # Operator-chosen channels via --showchannels. Each row labels
        # its block (Broadcast / Group N) so the LD can see at a glance
        # which fixture each value belongs to.
        for ch in state.show_channels:
            group_label, role = channel_to_role_and_group(ch)
            idx = ch - 1
            if 0 <= idx < len(payload):
                val = payload[idx]
                hex_str = f"0x{val:02X}"
                bar = "█" * max(1, val // 8)
                channels.add_row(
                    f"{ch:03d}", group_label, role, hex_str, str(val),
                    f"[green]{bar}[/green]",
                )
            else:
                channels.add_row(
                    f"{ch:03d}", group_label, role, "----", "-",
                    "[dim]no data[/dim]",
                )
        title = f"DMX channels ({len(state.show_channels)} selected)"
    else:
        # Default broadcast-block view (channels 1-23).
        for idx, role in enumerate(CHANNEL_ROLES[:23]):
            if idx < len(payload):
                val = payload[idx]
                hex_str = f"0x{val:02X}"
                bar = "█" * max(1, val // 8)
                channels.add_row(
                    f"{idx + 1:02d}", role, hex_str, str(val),
                    f"[green]{bar}[/green]",
                )
            else:
                channels.add_row(
                    f"{idx + 1:02d}", role, "----", "-",
                    "[dim]no data[/dim]",
                )
        title = "DMX channels (Broadcast block)"
    layout["channels"].update(Panel(channels, title=title, border_style="blue"))

    layout["footer"].update(Panel(
        Text("Ctrl+C to quit", justify="center", style="dim"),
        border_style="dim",
    ))
    return layout


# ============================================================================
# Main loop
# ============================================================================

def htp_merge(state: ShimState, now: float) -> bytes:
    """Per-channel max across all live (non-stale) sources in state.sources.

    Returns the merged DMX payload. Sources that haven't sent within
    MERGE_STALENESS_S are evicted before merging - so a producer
    stopping (QLC+ closing, orchestrator killed) cleanly hands the
    universe back to whoever's still alive instead of pinning a
    ghost frame on top.
    """
    cutoff = now - MERGE_STALENESS_S
    stale = [k for k, (_p, ts) in state.sources.items() if ts < cutoff]
    for k in stale:
        del state.sources[k]
    if not state.sources:
        return b""
    if len(state.sources) == 1:
        # Hot path - single source, no real merge needed.
        payload, _ts = next(iter(state.sources.values()))
        return payload
    merged = bytearray(DMX_UNIVERSE_BYTES)
    for payload, _ts in state.sources.values():
        n = min(len(payload), DMX_UNIVERSE_BYTES)
        for i in range(n):
            v = payload[i]
            if v > merged[i]:
                merged[i] = v
    return bytes(merged)


def run_loop(
    state: ShimState,
    sockets: list,
    serial_port_name: Optional[str],
    baud: int,
    on_tick: callable,
    encoding: str = "hex",
) -> None:
    """The pump. Reads UDP from all listening sockets, writes serial, calls
    on_tick once per cycle.

    `encoding` controls what goes on the cable:
      * "hex" (default) - ASCII hex of each Enttec Pro frame, terminated
        by a newline. Survives MicroPython's text-mode USB-CDC layer
        intact, so devices on either side (StickC firmware + Tildagon
        DmxBridge) see the same encoded protocol. The device firmware
        does the hex decode before feeding its parser.
      * "binary" - raw Enttec Pro bytes, no encoding. Lower bandwidth.
        Used when the device receives via a GPIO UART through an
        external FTDI cable (B7 future work) - the FTDI is binary-safe
        end-to-end so the encoding tax isn't justified.
    """
    ser: Optional[serial.Serial] = None
    last_serial_attempt = 0.0
    SERIAL_RETRY_INTERVAL = 1.0

    while True:
        # Drain all pending UDP packets from every bound stack (IPv4 + IPv6)
        # without blocking.
        for sock in sockets:
            while True:
                try:
                    data, addr = sock.recvfrom(2048)
                except BlockingIOError:
                    break
                # Epic 13: OpVendorEspNow passthrough. Inner frame goes
                # to the Stick wrapped as Enttec label 0x10; the Stick's
                # DMX bridge unwraps and broadcasts onto ESP-NOW.
                # Independent of merge / universe-filter logic - these
                # frames are 1:1 from author to radio.
                espnow_inner = decode_artnet_vendor_espnow(data)
                if espnow_inner is not None:
                    if ser is not None:
                        try:
                            framed = wrap_enttec_espnow(espnow_inner)
                            if encoding == "python":
                                ser.write(b'#"')
                                ser.write(binascii.hexlify(framed))
                                ser.write(b'"\n')
                            elif encoding == "hex":
                                ser.write(binascii.hexlify(framed))
                                ser.write(b";")
                            else:
                                ser.write(framed)
                            state.record_sent()
                        except (serial.SerialException, OSError) as e:
                            state.record_error(f"espnow write failed: {e}")
                            try:
                                ser.close()
                            except Exception:
                                pass
                            ser = None
                            state.serial_connected = False
                    continue

                decoded = decode_artnet_output(data)
                if decoded is None:
                    continue
                universe, payload = decoded
                if universe != state.universe:
                    state.frames_dropped_wrong_universe += 1
                    continue
                # `addr` is (host, port[, ...]); first two elements are
                # the producer's address family-agnostic identity.
                source_key = (addr[0], addr[1])
                # Merge layer. In "none" mode we forward the incoming
                # payload directly (the previous shim contract). In
                # "htp" mode we add this source's frame to the merge
                # map and emit the per-channel max across all live
                # sources, so orchestrator + QLC+ can co-drive the
                # rig without one process clobbering the other.
                if state.merge_mode == "htp":
                    state.sources[source_key] = (payload, time.monotonic())
                    payload = htp_merge(state, time.monotonic())
                    if not payload:
                        continue
                state.record_frame(payload)

                # Try to send if serial is up. Apply the cable encoding
                # chosen at startup. Three modes, pick determined by
                # --encode (auto-resolves based on device kind):
                #   "python"  -> "#<hex>\n" - a Python comment + NL.
                #                Tildagons need this because their
                #                USB-CDC IS the MicroPython REPL
                #                channel; valid-Python bytes get
                #                no-op'd by the REPL rather than
                #                causing tracebacks (which hang the
                #                device).
                #   "hex"     -> "<hex>;" - generic ASCII-safe; no
                #                REPL friendliness.
                #   "binary"  -> raw Enttec bytes; lowest bandwidth;
                #                used by Stick firmware reading binary
                #                serial directly.
                if ser is not None:
                    try:
                        framed = wrap_enttec_pro(payload)
                        if encoding == "python":
                            # Envelope: #"<hex>"\n
                            #   # opens a Python comment (REPL no-ops)
                            #   " starts the "string" inside the comment
                            #     and IS what the device parser uses as
                            #     the frame terminator
                            #   \n lets the REPL move on between frames
                            # The frame boundaries are the " marks,
                            # because \n is consumed by the REPL.
                            ser.write(b'#"')
                            ser.write(binascii.hexlify(framed))
                            ser.write(b'"\n')
                        elif encoding == "hex":
                            ser.write(binascii.hexlify(framed))
                            ser.write(b";")
                        else:
                            ser.write(framed)
                        state.record_sent()
                    except (serial.SerialException, OSError) as e:
                        state.record_error(f"write failed: {e}")
                        try:
                            ser.close()
                        except Exception:
                            pass
                        ser = None
                        state.serial_connected = False

        # Open / re-open serial if disconnected. Auto-detect if no explicit
        # port was set OR re-scan in case the operator plugged it in a
        # different USB slot.
        now = time.monotonic()
        if ser is None and (now - last_serial_attempt) >= SERIAL_RETRY_INTERVAL:
            last_serial_attempt = now
            chosen = serial_port_name
            if chosen is None:
                candidates = find_candidate_ports()
                chosen = candidates[0] if candidates else None
            state.serial_port = chosen
            if chosen is not None:
                ser = open_serial(chosen, baud)
                state.serial_connected = ser is not None

        state.tick_fps()
        on_tick()
        time.sleep(0.005)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge QLC+ Art-Net output to a NocturNation StickC "
            "(Plus2 or S3) running in DMX Bridge mode."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--port", default=None,
        help="StickC serial port to write to (auto-detected if omitted). "
             "macOS: /dev/cu.usbmodem* (S3) or /dev/cu.usbserial-* (Plus2). "
             "Linux: /dev/ttyACM* or /dev/ttyUSB*. Windows: COMn. "
             "Tildagon ports are filtered out (DMX Bridge is StickC-only).",
    )
    parser.add_argument(
        "--baud", type=int, default=ENTTEC_BAUD,
        help="Serial baud rate.",
    )
    parser.add_argument(
        "--artnet-port", type=int, default=ARTNET_DEFAULT_PORT,
        help="UDP port to listen on for Art-Net packets.",
    )
    parser.add_argument(
        "--bind", default="0.0.0.0",
        help="UDP bind address.",
    )
    parser.add_argument(
        "--universe", type=int, default=1,
        help="Art-Net universe to filter on (0-32767). Default 1 matches "
             "QLC+ (universes are 1-indexed in the QLC+ UI) and the "
             "nowplaying-orchestrator's --artnet-universe default.",
    )
    parser.add_argument(
        "--merge",
        choices=["none", "htp"],
        default="none",
        help="Multi-source merge mode. 'none' (default) forwards whichever "
             "frame arrived last - safe with a single producer. 'htp' "
             "(Highest-Takes-Precedence) keeps per-source state and "
             "emits per-channel max across all live producers, so the "
             "orchestrator's cue-driven bed and QLC+'s manual overrides "
             "can coexist (e.g. push a button to fire a pulse without "
             "disturbing the running show). A source idle for "
             f"{MERGE_STALENESS_S:.1f}s drops out of the merge.",
    )
    parser.add_argument(
        "--no-ui", action="store_true",
        help="Disable the rich terminal UI; print plain text status only.",
    )
    parser.add_argument(
        "--encode",
        choices=["auto", "binary", "hex", "python"],
        default="auto",
        help="Cable protocol. 'auto' (default) picks based on the port's "
             "device description: 'python' for Tildagon (its USB-CDC is "
             "the MicroPython REPL channel, so we disguise frames as "
             "Python comments the REPL no-ops on), 'binary' for everything "
             "else. 'python' wraps each Enttec Pro frame as '#<hex>\\n' - "
             "a comment + newline, valid Python that the REPL parses, "
             "evaluates as a no-op, and leaves intact for our line "
             "reader. 'hex' uses '<hex>;' (no REPL-friendliness). "
             "'binary' writes raw Enttec Pro bytes - lower bandwidth, "
             "requires the device to do binary serial reads (Stick "
             "firmware reading from a hardware UART via external FTDI, "
             "see B7).",
    )
    parser.add_argument(
        "--showchannels", default=None,
        help="Comma-separated list of universe channels to display in "
             "the debug panel instead of the default broadcast block "
             "(channels 1-23). Each channel is 1-indexed (1..512); "
             "the panel shows the channel number, its block (Broadcast "
             "/ Group N), its role (Master, Raw R, etc.), and the live "
             "value. Useful when the LD has patched fixtures in "
             "non-broadcast groups (Group 1 at 41, Group 2 at 81, ...) "
             "and wants to watch those specifically. "
             "Example: --showchannels 24,25,26,27,64,65,66,67",
    )
    args = parser.parse_args()

    # Parse --showchannels into a validated list of ints, or None if
    # the flag wasn't passed (default broadcast view).
    show_channels = None
    if args.showchannels is not None:
        try:
            show_channels = [int(c.strip()) for c in args.showchannels.split(",") if c.strip()]
        except ValueError:
            print("--showchannels must be a comma-separated list of integers",
                  file=sys.stderr)
            sys.exit(2)
        for ch in show_channels:
            if not (1 <= ch <= MAX_UNIVERSE_CHANNEL):
                print(f"--showchannels: channel {ch} out of range (1..{MAX_UNIVERSE_CHANNEL})",
                      file=sys.stderr)
                sys.exit(2)
        if not show_channels:
            show_channels = None  # empty list -> fall back to default view

    # Bind BOTH IPv4 and IPv6 wildcards on port 6454.
    #
    # Why both: on macOS, QLC+'s Art-Net plugin binds [::]:6454 (IPv6
    # wildcard) with default IPV6_V6ONLY=0. That dual-stack socket
    # silently shadows ALL IPv4 loopback traffic to port 6454 via IPv4-
    # mapped-IPv6 delivery - so if QLC+ wins the IPv6 race, packets sent
    # to 127.0.0.1:6454 are delivered to QLC+'s listener and our IPv4-
    # only socket sees nothing (bench-confirmed via lsof + netcat).
    #
    # By grabbing both stacks at startup (IPv6 bound with IPV6_V6ONLY=1
    # so it doesn't double-receive our IPv4 traffic), QLC+'s subsequent
    # [::]:6454 bind fails. QLC+ logs a harmless warning, falls back to
    # send-only, and its output to 127.0.0.1:6454 reaches our IPv4
    # socket cleanly.
    sockets = []

    sock4 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock4.bind((args.bind, args.artnet_port))
        sock4.setblocking(False)
        sockets.append(sock4)
    except OSError as e:
        print(f"Failed to bind IPv4 UDP {args.bind}:{args.artnet_port}: {e}",
              file=sys.stderr)
        if e.errno == 48:   # EADDRINUSE on macOS
            print(
                "\nUDP 6454 is already in use by another process. Diagnose:\n"
                "  lsof -i UDP:6454\n"
                "\nIf it's a stale shim, kill it:\n"
                "  pkill -f artnet-to-enttec-pro\n"
                "\nIf it's qlcplus, quit QLC+, start the shim first, then\n"
                "relaunch QLC+. QLC+'s Art-Net input bind will fail (it\n"
                "logs a warning and continues as send-only); QLC+'s\n"
                "output to 127.0.0.1:6454 then reaches the shim.",
                file=sys.stderr,
            )
        return 1

    try:
        sock6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock6.bind(("::", args.artnet_port))
        sock6.setblocking(False)
        sockets.append(sock6)
    except OSError as e:
        # Non-fatal but critical to warn about on macOS - if we couldn't
        # grab IPv6 and QLC+ is running, QLC+'s [::] listener will steal
        # all loopback traffic via dual-stack mapping.
        print(
            f"Warning: IPv6 bind failed ({e}).\n"
            "On macOS this likely means another process (probably QLC+)\n"
            "already owns [::]:6454 and will intercept loopback Art-Net\n"
            "packets via dual-stack mapping before they reach this shim.\n"
            "Quit QLC+, restart this shim, then relaunch QLC+.",
            file=sys.stderr,
        )

    # Resolve serial port: explicit --port wins; otherwise, if
    # interactive (not --no-ui), prompt the user to pick from the
    # detected candidates. Headless mode falls through to the run
    # loop's existing auto-detect-first-port behaviour.
    chosen_port = args.port
    if chosen_port is None and not args.no_ui:
        chosen_port = interactive_pick_port()
        if chosen_port is None:
            print("No port selected; exiting.", file=sys.stderr)
            for s in sockets:
                try:
                    s.close()
                except Exception:
                    pass
            return 1

    # Hard guard against pointing the shim at a Tildagon. The badge's
    # USB-CDC endpoint is owned by the OS REPL, not by a DMX Bridge
    # mode (the Tildagon firmware has no such mode). Writing Enttec
    # frames into the REPL would just spam the console and confuse the
    # operator into thinking the radio is silent. Tildagons are
    # filtered from auto-detection; this guard handles the case where
    # the operator passes --port explicitly.
    if chosen_port is not None:
        for port_info in serial.tools.list_ports.comports():
            if port_info.device == chosen_port and port_looks_like_tildagon(port_info):
                print(
                    f"Refusing to connect: {chosen_port} appears to be a "
                    f"Tildagon ({describe_port(port_info)}). The DMX "
                    f"Bridge role is StickC-only - the Tildagon "
                    f"firmware has no DMX Bridge mode. Plug in a "
                    f"StickC and try again.",
                    file=sys.stderr,
                )
                for s in sockets:
                    try:
                        s.close()
                    except Exception:
                        pass
                return 1
            if port_info.device == chosen_port:
                break

    # Resolve --encode auto: Tildagons need python-comment envelope
    # because their USB-CDC IS the MicroPython REPL channel; everything
    # else gets binary by default (Stick firmware reads raw bytes).
    chosen_encoding = args.encode
    if chosen_encoding == "auto":
        if chosen_port:
            kind = device_kind_from_port(chosen_port)
            chosen_encoding = "python" if kind == "tildagon" else "binary"
        else:
            chosen_encoding = "binary"
        print(f"Encoding: auto-selected '{chosen_encoding}' "
              f"(port: {chosen_port or '<not yet resolved>'})")

    state = ShimState(
        serial_port=chosen_port,
        bind_addr=args.bind,
        artnet_port=args.artnet_port,
        universe=args.universe,
        merge_mode=args.merge,
        show_channels=show_channels,
    )

    console = Console()
    try:
        if args.no_ui:
            print(f"Shim {VERSION}  Art-Net {args.bind}:{args.artnet_port} "
                  f"-> serial (universe {args.universe})  "
                  f"Press Ctrl+C to quit.")
            last_print = 0.0

            def headless_tick():
                nonlocal last_print
                now = time.monotonic()
                if now - last_print >= 5.0:
                    last_print = now
                    # Include wrong-universe drops - a non-zero value here
                    # while in= stays at zero is the classic "QLC+ is
                    # sending universe 0 but shim filters universe 1"
                    # signal. Saved hours during the B3.5 bench session.
                    drops = state.frames_dropped_wrong_universe
                    drop_str = f" drops={drops}" if drops else ""
                    print(
                        f"[{time.strftime('%H:%M:%S')}] "
                        f"serial={'OK' if state.serial_connected else 'WAIT'} "
                        f"in={state.frames_received} out={state.frames_sent} "
                        f"fps={state.fps:.1f} err={state.errors}{drop_str}"
                    )

            run_loop(state, sockets, chosen_port, args.baud,
                     headless_tick, encoding=chosen_encoding)
        else:
            with Live(
                build_status_layout(state),
                console=console,
                refresh_per_second=5,
                screen=True,
            ) as live:
                def ui_tick():
                    live.update(build_status_layout(state))
                run_loop(state, sockets, chosen_port, args.baud,
                         ui_tick, encoding=chosen_encoding)
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down.[/yellow]")
    finally:
        for s in sockets:
            try:
                s.close()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
