# NocturNation laptop-side tools

## `artnet-to-enttec-pro.py`

The shim that bridges QLC+ (or any Art-Net-capable lighting console) to a
NocturNation StickC or Tildagon running in DMX Bridge mode.

```
QLC+  ->  Art-Net UDP  ->  this shim  ->  Enttec Pro USB-CDC  ->  device
```

See `docs/qlc-plus-beginners-guide.md` for the full operator walk-through.
The notes below cover running and configuring the shim itself.

### Quick start

**macOS / Linux:**

```sh
./run-macos.sh
```

**Windows:**

```bat
run-windows.bat
```

First invocation creates a local Python venv under `tools/.venv` and
installs `pyserial` + `rich` (~10 seconds, one-off). Subsequent invocations
start immediately.

By default the shim auto-detects the first `/dev/cu.usbmodem*` (macOS),
`/dev/ttyACM*` (Linux), or `COMn` (Windows) and listens for Art-Net on UDP
6454, universe 1. Override anything via CLI:

```sh
./run-macos.sh --port /dev/cu.usbmodem1101 --universe 2 --artnet-port 6454
```

Full options:

```
--port PORT          Serial port (auto-detected if omitted)
--baud BAUD          Serial baud rate (default: 921600)
--artnet-port PORT   UDP port to listen on (default: 6454)
--bind ADDR          UDP bind address (default: 0.0.0.0)
--universe N         Art-Net universe to filter on (default: 1)
--no-ui              Disable the rich terminal UI; print plain status only
```

### QLC+ side

Configure QLC+'s Art-Net plugin to output to `127.0.0.1` (or `localhost`)
on universe 1. The shim listens on all interfaces by default, so any
network-reachable address also works if you want to drive the shim from a
different machine.

**Universe mapping.** QLC+'s Art-Net plugin configuration shows two
universe-related fields per output row: "Universe" (QLC+'s internal
universe, 1-indexed) and "ArtNet Universe" (the value stamped in the
wire packet, 0-indexed by default). So a default patch of QLC+ Universe
1 -> "ArtNet Universe 0" puts the packet on the wire as **wire universe
0**, not 1. The shim defaults to `--universe 0` to match QLC+'s
default. If you change QLC+'s "ArtNet Universe" to a different value,
pass the matching `--universe N` to the shim.

If the headless output shows `in=0 drops=N` with N climbing, you've
got a universe mismatch - the shim is receiving packets and dropping
them because they're on a universe it isn't watching.

**Port hogging.** The shim binds UDP 6454 exclusively on both IPv4
and IPv6 stacks to prevent QLC+'s dual-stack listener from intercepting
loopback packets. That means **no other Art-Net receiver on this
machine can run while the shim is up** - including a second QLC+
instance, MagicQ, Resolume, or any other lighting console. (Art-Net
fundamentally uses one port for all universes; sharing the port among
multiple receiver applications isn't how the protocol works.) QLC+ is
the special case: its Art-Net plugin gracefully falls back to
send-only when it can't bind for input. Other apps may abort entirely.
**Operational rule: only run the shim while actively bridging
NocturNation.**

**Important: launch order is shim FIRST, then QLC+.** QLC+'s Art-Net
plugin binds UDP 6454 for input as a side-effect even if you only want
output, and on macOS its IPv6 wildcard bind shadows our IPv4 bind via
dual-stack mapping if it claims the port first - the shim would then
appear running but receive zero packets. With the shim bound first,
QLC+'s input bind fails silently (a harmless warning in its log) and
its output to `127.0.0.1:6454` reaches the shim cleanly. If you forget
the order, the shim's bind will fail with EADDRINUSE and prompt you on
how to recover.

### What the shim shows

The default `rich` terminal UI has two panels.

**Status panel**  - connection state, Art-Net frames received, Enttec
frames sent, frame rate (fps), wrong-universe drops, error count, last
error message.

**DMX channels panel** - the 12-channel NocturNation fixture layout with
the value of each channel from the most recent Art-Net frame: master
intensity, strobe, pulse R/G/B/trigger, wash A R/G/B, wash B R/G/B. Bar
meter alongside each value so you can see at a glance what QLC+ is sending.

If the device is unplugged, the Serial line shows "no device detected" or
"waiting for ..."; the shim retries every second and recovers when the
device reappears - no restart needed.

### Headless / log-only mode

If running under a process manager, on a headless venue PC, or in CI:

```sh
./run-macos.sh --no-ui
```

Prints a one-line status summary to stdout every five seconds; no
terminal-cursor magic. Easier to log + grep.

### Requirements

- Python 3.9 or later
- pyserial 3.5+
- rich 13.0+

The wrapper scripts install the Python deps automatically into a local
venv on first run. You can also install manually:

```sh
python3 -m pip install -r requirements.txt
python3 artnet-to-enttec-pro.py
```

### License

BSD-3-Clause. References the Art-Net packet decode pattern from
[mich181189/Tildagon-ArtNet](https://github.com/mich181189/Tildagon-ArtNet)
(same license).
