"""Now-playing OS backends.

The orchestrator polls one of these every ~1 s to find out what the
user's local audio app is playing. The result drives cue file
matching and the position-anchored cue scheduler.

`base.py` defines the platform-agnostic interface; per-OS modules
implement it. The orchestrator's main loop picks the right backend
for the host platform (or falls back to a fixed-position simulator
for testing).
"""

from .base import NowPlaying, NowPlayingBackend, NowPlayingError

__all__ = [
    "NowPlaying",
    "NowPlayingBackend",
    "NowPlayingError",
]
