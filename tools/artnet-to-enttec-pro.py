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


# ============================================================================
# Constants
# ============================================================================

VERSION = "0.1.0"

# Art-Net protocol
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_OP_OUTPUT = 0x5000
ARTNET_DEFAULT_PORT = 6454

# Enttec DMX USB Pro framing
ENTTEC_START = 0x7E
ENTTEC_END = 0xE7
ENTTEC_LABEL_OUTPUT = 0x06
ENTTEC_DMX_START_CODE = 0x00
ENTTEC_BAUD = 921_600
DMX_UNIVERSE_BYTES = 512

# Channel role labels for the diagnostics view, matching Epic 7 Q2 layout.
CHANNEL_ROLES = [
    "Master",      "Strobe",      "Pulse R",     "Pulse G",
    "Pulse B",     "Pulse Trig",  "Wash A R",    "Wash A G",
    "Wash A B",    "Wash B R",    "Wash B G",    "Wash B B",
]


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


# ============================================================================
# Enttec Pro framing
# ============================================================================

def wrap_enttec_pro(payload: bytes) -> bytes:
    """Wrap a DMX payload in Enttec DMX USB Pro Output Only framing.

    Pads payload to 512 bytes if shorter; truncates if longer (Art-Net can
    send 2-512 channels per packet). The length field in the Enttec frame
    is the payload size INCLUDING the DMX start code byte (513 total).

    Layout:
      0     0x7E                    start byte
      1     0x06                    label = Output Only Send DMX Packet
      2..3  513 (little-endian)     length = start code + 512 channel bytes
      4     0x00                    DMX start code (standard data, no RDM)
      5..516 channel values 1..512
      517   0xE7                    end byte
    """
    if len(payload) < DMX_UNIVERSE_BYTES:
        payload = payload + b"\x00" * (DMX_UNIVERSE_BYTES - len(payload))
    elif len(payload) > DMX_UNIVERSE_BYTES:
        payload = payload[:DMX_UNIVERSE_BYTES]
    length = DMX_UNIVERSE_BYTES + 1   # 1 byte start code + 512 channels
    return bytes((
        ENTTEC_START,
        ENTTEC_LABEL_OUTPUT,
        length & 0xFF,
        (length >> 8) & 0xFF,
        ENTTEC_DMX_START_CODE,
    )) + payload + bytes((ENTTEC_END,))


# ============================================================================
# Serial port handling
# ============================================================================

def find_candidate_ports() -> list[str]:
    """List likely NocturNation device serial ports on the current OS.

    On macOS, USB-CDC devices appear as /dev/cu.usbmodemNNNN. On Linux,
    /dev/ttyACMn. On Windows, USB-CDC devices show as COMn with a
    description containing 'USB Serial' or the device's product string -
    we just return all COM ports and let the user pick if there's more
    than one.
    """
    candidates = []
    for port in serial.tools.list_ports.comports():
        device = port.device
        if "usbmodem" in device or "ttyACM" in device:
            candidates.append(device)
        elif device.upper().startswith("COM"):   # Windows
            candidates.append(device)
    return candidates


def open_serial(port: str, baud: int) -> Optional[serial.Serial]:
    """Try to open a serial port; return None on failure (silent)."""
    try:
        return serial.Serial(port, baud, timeout=0, write_timeout=0.1)
    except (serial.SerialException, OSError):
        return None


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

    def record_frame(self, payload: bytes) -> None:
        now = time.monotonic()
        self.frames_received += 1
        self.last_artnet_ts = now
        self.last_payload = payload
        self._frame_times.append(now)
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

    # Channels panel: 12-row hex view with bar meters (NocturNation layout).
    channels = Table(show_header=True, expand=True, padding=(0, 1))
    channels.add_column("Ch", style="dim", width=3)
    channels.add_column("Role", width=11)
    channels.add_column("Hex", width=4)
    channels.add_column("Val", width=4)
    channels.add_column("Level", ratio=1)
    payload = state.last_payload or b""
    for idx, role in enumerate(CHANNEL_ROLES):
        if idx < len(payload):
            val = payload[idx]
            hex_str = f"0x{val:02X}"
            bar_width = max(1, val // 8)   # 0..32 chars wide
            bar = "█" * bar_width
            channels.add_row(
                f"{idx + 1:02d}", role, hex_str, str(val),
                f"[green]{bar}[/green]",
            )
        else:
            channels.add_row(f"{idx + 1:02d}", role, "----", "-", "[dim]no data[/dim]")
    layout["channels"].update(Panel(channels, title="DMX channels", border_style="blue"))

    layout["footer"].update(Panel(
        Text("Ctrl+C to quit", justify="center", style="dim"),
        border_style="dim",
    ))
    return layout


# ============================================================================
# Main loop
# ============================================================================

def run_loop(
    state: ShimState,
    sock: socket.socket,
    serial_port_name: Optional[str],
    baud: int,
    on_tick: callable,
) -> None:
    """The pump. Reads UDP, writes serial, calls on_tick once per cycle."""
    ser: Optional[serial.Serial] = None
    last_serial_attempt = 0.0
    SERIAL_RETRY_INTERVAL = 1.0

    while True:
        # Drain all pending UDP packets without blocking.
        while True:
            try:
                data, _addr = sock.recvfrom(2048)
            except BlockingIOError:
                break
            decoded = decode_artnet_output(data)
            if decoded is None:
                continue
            universe, payload = decoded
            if universe != state.universe:
                state.frames_dropped_wrong_universe += 1
                continue
            state.record_frame(payload)

            # Try to send if serial is up.
            if ser is not None:
                try:
                    ser.write(wrap_enttec_pro(payload))
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

        on_tick()
        time.sleep(0.005)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bridge QLC+ Art-Net output to a NocturNation StickC or Tildagon "
            "running in DMX Bridge mode."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--port", default=None,
        help="Serial port to write to (auto-detected if omitted; "
             "/dev/cu.usbmodem* on macOS, /dev/ttyACM* on Linux, COMn on Windows).",
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
        help="Art-Net universe to filter on (0-32767).",
    )
    parser.add_argument(
        "--no-ui", action="store_true",
        help="Disable the rich terminal UI; print plain text status only.",
    )
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((args.bind, args.artnet_port))
    except OSError as e:
        print(f"Failed to bind UDP {args.bind}:{args.artnet_port}: {e}",
              file=sys.stderr)
        return 1
    sock.setblocking(False)

    state = ShimState(
        serial_port=args.port,
        bind_addr=args.bind,
        artnet_port=args.artnet_port,
        universe=args.universe,
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
                    print(
                        f"[{time.strftime('%H:%M:%S')}] "
                        f"serial={'OK' if state.serial_connected else 'WAIT'} "
                        f"in={state.frames_received} out={state.frames_sent} "
                        f"fps={state.fps:.1f} err={state.errors}"
                    )

            run_loop(state, sock, args.port, args.baud, headless_tick)
        else:
            with Live(
                build_status_layout(state),
                console=console,
                refresh_per_second=5,
                screen=True,
            ) as live:
                def ui_tick():
                    live.update(build_status_layout(state))
                run_loop(state, sock, args.port, args.baud, ui_tick)
    except KeyboardInterrupt:
        console.print("\n[yellow]Shutting down.[/yellow]")
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
