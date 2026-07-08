#!/usr/bin/env bash
# NocturNation audio-enrich wrapper - macOS.
#
# First run: installs librosa + pyserial + rich into a local venv under
# tools/.venv (shared with the orchestrator + Art-Net shim wrappers).
# Subsequent runs: just uses the venv Python.
# Forwards all CLI args to scripts/audio_enrich_cues.py.
#
# Usage from Docs/:
#   tools/run-audio-enrich-macos.sh songs/track.cues --audio path/to/track.flac
#
# Or from Docs/songs/ (auto-discovery of a same-basename FLAC alongside
# the cue file):
#   ../tools/run-audio-enrich-macos.sh track.cues
#
# Also needs ffmpeg on PATH (`brew install ffmpeg`) for non-WAV decode
# (FLAC, MP3, M4A, OGG). WAV works without ffmpeg.
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

# Detect a stale venv (Python upgrade can leave a dangling
# bin/python symlink). Rebuild if the Python is gone.
if [ -d "$VENV" ] && [ ! -x "$VENV/bin/python" ]; then
    echo "Existing tools/.venv is stale (its Python is gone); rebuilding."
    rm -rf "$VENV"
fi

if [ ! -d "$VENV" ]; then
    echo "Creating local venv at tools/.venv (one-off, ~30 seconds for librosa)."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$HERE/requirements.txt"
    echo "Venv ready."
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "warning: ffmpeg not found on PATH." >&2
    echo "  install with: brew install ffmpeg" >&2
    echo "  needed to decode FLAC / MP3 / M4A / OGG (WAV works without it)." >&2
fi

exec "$VENV/bin/python" "$HERE/scripts/audio_enrich_cues.py" "$@"
