#!/usr/bin/env python3
"""NocturNation now-playing orchestrator (Epic 10).

Watches the local OS's now-playing audio, looks up the track in
`Docs/songs/<artist>-<title>.cues`, runs the cue list through the FX
engine, and emits DMX universe state as either Enttec Pro frames over
USB-CDC or Art-Net to a co-running shim.

Run::

    Docs/tools/nowplaying-orchestrator.py
    Docs/tools/nowplaying-orchestrator.py --output artnet
    Docs/tools/nowplaying-orchestrator.py --songs-dir /path/to/songs

Modes:

    --output auto     (default) try USB; fall back to Art-Net (127.0.0.1:6454)
    --output usb      USB direct (Enttec Pro framing). Requires StickC plugged in.
    --output artnet   Art-Net producer to 127.0.0.1:6454 (or --artnet-host/port).

Now-playing backends are picked by host platform. macOS uses
`nowplaying-cli` (`brew install nowplaying-cli`); Windows / Linux
backends land in B6.

Ctrl+C exits cleanly.
"""

import argparse
import platform
import sys
from pathlib import Path

# Allow `python3 nowplaying-orchestrator.py` from any cwd.
_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

from nocturnation_orchestrator.main import run
from nocturnation_orchestrator.nowplaying import NowPlayingError
from nocturnation_orchestrator.output import create_dispatcher


_DEFAULT_SONGS_DIR = _TOOLS_DIR.parent / "songs"


def build_nowplaying_backend(args):
    system = platform.system()
    if system == "Darwin":
        from nocturnation_orchestrator.nowplaying.macos import MacOSBackend
        backend = MacOSBackend(binary=args.nowplaying_binary)
        backend.ensure_available()
        return backend
    if system == "Windows":
        from nocturnation_orchestrator.nowplaying.windows import WindowsBackend
        backend = WindowsBackend()
        backend.ensure_available()
        return backend
    if system == "Linux":
        from nocturnation_orchestrator.nowplaying.linux import LinuxBackend
        backend = LinuxBackend()
        backend.ensure_available()
        return backend
    sys.exit(
        "now-playing backend for %s is not supported; the orchestrator runs "
        "on macOS (nowplaying-cli), Windows (winsdk / SMTC), and Linux "
        "(pydbus / MPRIS)." % system
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="NocturNation now-playing orchestrator",
    )
    parser.add_argument(
        "--songs-dir", default=str(_DEFAULT_SONGS_DIR),
        help="directory of .cues files (default: %(default)s)",
    )
    parser.add_argument(
        "--output", choices=("auto", "usb", "artnet"), default="auto",
        help="output mode (default: %(default)s)",
    )
    parser.add_argument(
        "--usb-port", default=None,
        help="explicit USB serial port (default: first StickC match)",
    )
    parser.add_argument(
        "--usb-baud", type=int, default=None,
        help="USB baud override (default: nocturnation_dmx default, 460800)",
    )
    parser.add_argument(
        "--artnet-host", default="127.0.0.1",
        help="Art-Net target host (default: %(default)s)",
    )
    parser.add_argument(
        "--artnet-port", type=int, default=6454,
        help="Art-Net target port (default: %(default)s)",
    )
    parser.add_argument(
        "--artnet-universe", type=int, default=1,
        help="Art-Net universe to emit on. Matches QLC+ / shim defaults "
             "(default: %(default)s)",
    )
    parser.add_argument(
        "--default-bpm", type=int, default=120,
        help="fallback BPM if neither @bpm nor a per-cue --bpm is set",
    )
    parser.add_argument(
        "--nowplaying-binary", default="nowplaying-cli",
        help="path to nowplaying-cli binary (macOS only)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="emit one log line per cue fire / lyric anchor / now-playing poll "
             "so you can trace what the cue file is doing in real time",
    )
    args = parser.parse_args(argv)

    try:
        backend = build_nowplaying_backend(args)
    except NowPlayingError as exc:
        sys.exit("error: %s" % exc)

    dispatcher = create_dispatcher(
        mode=args.output,
        usb_port=args.usb_port,
        usb_baud=args.usb_baud,
        artnet_host=args.artnet_host,
        artnet_port=args.artnet_port,
        artnet_universe=args.artnet_universe,
    )

    try:
        run(
            nowplaying_backend=backend,
            dispatcher=dispatcher,
            songs_dir=args.songs_dir,
            default_bpm=args.default_bpm,
            debug=args.debug,
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
