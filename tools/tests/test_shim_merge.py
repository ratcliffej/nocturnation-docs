"""Tests for the artnet-to-enttec-pro shim's HTP merge layer.

The shim is a script, not an importable package - we load it via
importlib so the tests exercise the live module without forcing the
shim to be packaged. The merge logic (htp_merge, ShimState.sources,
MERGE_STALENESS_S) is pure-Python and doesn't need sockets or serial.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SHIM_PATH = Path(__file__).resolve().parent.parent / "artnet-to-enttec-pro.py"


def _load_shim():
    spec = importlib.util.spec_from_file_location("artnet_shim", _SHIM_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def shim():
    return _load_shim()


def _payload(channels: dict[int, int]) -> bytes:
    """Build a 512-byte universe with the given channel: value pairs.
    Keys are 1-indexed DMX channels to match the rest of the codebase."""
    u = bytearray(512)
    for ch, v in channels.items():
        u[ch - 1] = v
    return bytes(u)


class TestHtpMerge:
    def test_single_source_returns_its_payload(self, shim):
        state = shim.ShimState()
        state.sources[("127.0.0.1", 1000)] = (_payload({1: 100, 6: 50}), 10.0)
        merged = shim.htp_merge(state, now=10.0)
        assert merged[0] == 100   # ch 1
        assert merged[5] == 50    # ch 6
        # Identity path - the merge returns the same object the
        # caller stored, avoiding a 512-byte copy when only one
        # source is live (the common case).
        assert merged is state.sources[("127.0.0.1", 1000)][0]

    def test_two_sources_per_channel_max(self, shim):
        state = shim.ShimState()
        # Orchestrator's "bed" frame.
        state.sources[("127.0.0.1", 1000)] = (
            _payload({1: 100, 11: 50, 12: 0, 13: 50}), 10.0,
        )
        # QLC+'s "manual pulse" frame - sets pulse trigger + RGB
        # but doesn't touch the wash channels.
        state.sources[("127.0.0.1", 2000)] = (
            _payload({3: 255, 4: 255, 5: 255, 6: 255}), 10.0,
        )
        merged = shim.htp_merge(state, now=10.0)
        # Orchestrator's wash channels survive (QLC+ didn't set them).
        assert merged[10] == 50   # ch 11
        assert merged[12] == 50   # ch 13
        # QLC+'s pulse trigger + RGB land on top.
        assert merged[2] == 255   # ch 3 (pulse R)
        assert merged[5] == 255   # ch 6 (pulse trig)
        # Master takes whichever value is higher.
        assert merged[0] == 100

    def test_stale_source_evicted(self, shim):
        state = shim.ShimState()
        state.sources[("127.0.0.1", 1000)] = (_payload({1: 200}), 10.0)
        # 1 second later, the source hasn't sent - it's past
        # MERGE_STALENESS_S and should be dropped.
        merged = shim.htp_merge(state, now=10.0 + shim.MERGE_STALENESS_S + 0.5)
        assert merged == b""
        assert ("127.0.0.1", 1000) not in state.sources

    def test_stale_source_doesnt_pin_other_sources(self, shim):
        state = shim.ShimState()
        # An "old" QLC+ frame at master=255.
        state.sources[("127.0.0.1", 1000)] = (_payload({1: 255}), 10.0)
        # A "fresh" orchestrator frame at master=80.
        state.sources[("127.0.0.1", 2000)] = (
            _payload({1: 80}), 10.0 + shim.MERGE_STALENESS_S + 0.4,
        )
        # When merging at the fresh frame's wall-clock, QLC+ is stale -
        # so its master=255 must NOT win. The orchestrator's 80 wins
        # because it's the only live source.
        merged = shim.htp_merge(
            state, now=10.0 + shim.MERGE_STALENESS_S + 0.4,
        )
        assert merged[0] == 80
        # The stale source is gone.
        assert ("127.0.0.1", 1000) not in state.sources
        assert ("127.0.0.1", 2000) in state.sources

    def test_empty_state_returns_empty(self, shim):
        state = shim.ShimState()
        assert shim.htp_merge(state, now=0.0) == b""

    def test_state_default_merge_mode_is_none(self, shim):
        """Single-source operators (existing users) must see no
        behaviour change. The merge layer is opt-in."""
        state = shim.ShimState()
        assert state.merge_mode == "none"
        assert state.sources == {}
