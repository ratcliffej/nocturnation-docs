#!/usr/bin/env bash
# NocturNation now-playing orchestrator - macOS wrapper.
#
# First run: installs pyserial + rich into a local venv under tools/.venv
# (shared with the Art-Net shim wrapper, run-macos.sh).
# Subsequent runs: just activates the venv and starts the orchestrator.
# Forwards all CLI args to the orchestrator.
#
# Also requires nowplaying-cli on PATH (`brew install nowplaying-cli`)
# for the macOS now-playing source.
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

if ! command -v nowplaying-cli >/dev/null 2>&1; then
    echo "warning: nowplaying-cli not found on PATH." >&2
    echo "  install with: brew install nowplaying-cli" >&2
    echo "  the orchestrator can't read what's playing without it." >&2
fi

exec "$VENV/bin/python" "$HERE/nowplaying-orchestrator.py" "$@"
