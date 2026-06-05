#!/usr/bin/env bash
# NocturNation Art-Net to Enttec Pro shim - macOS wrapper.
#
# First run: installs pyserial + rich into a local venv under tools/.venv.
# Subsequent runs: just activates the venv and starts the shim.
# Forwards all CLI args to the shim.
#
# Requires Python 3.9+ (macOS 13+ ships Python 3 by default; install from
# python.org if not present).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.venv"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 not found. Install from https://www.python.org/downloads/macos/" >&2
    exit 1
fi

if [ ! -d "$VENV" ]; then
    echo "First run: creating local venv at tools/.venv (one-off, ~10 seconds)."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
    echo "Venv ready."
fi

exec "$VENV/bin/python" "$HERE/artnet-to-enttec-pro.py" "$@"
