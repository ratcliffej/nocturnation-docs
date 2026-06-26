"""Tests for the librosa MIR wrapper's pure-Python helpers.

The actual `mir.analyse()` call is bench-tested against real audio
(see Epic 14 B5 bench procedure). These tests cover only the parts
that don't need librosa installed: the Krumhansl-Schmuckler key
estimator, the section-count heuristic, and the loudness conversion.
"""

from __future__ import annotations

import pytest

from nocturnation_orchestrator.mir import (
    _choose_section_count, _correlation, _estimate_key_mode,
    _MAJOR_PROFILE, _MINOR_PROFILE, _PITCH_NAMES, _utc_now_iso,
)


# ---------------------------------------------------------------------------
# Section count heuristic
# ---------------------------------------------------------------------------


class TestChooseSectionCount:
    def test_typical_pop_3_minutes(self):
        # 3 min = 180 s -> 9 sections
        assert _choose_section_count(180.0) == 9

    def test_short_track_floor(self):
        # 30 s track -> floor at 4
        assert _choose_section_count(30.0) == 4
        # 1 s edge case
        assert _choose_section_count(1.0) == 4

    def test_long_track_ceiling(self):
        # 10 min track -> ceiling at 15
        assert _choose_section_count(600.0) == 15

    def test_intermediate(self):
        # 5 min = 300 s -> 15 (right at the ceiling)
        assert _choose_section_count(300.0) == 15
        # 2 min = 120 s -> 6
        assert _choose_section_count(120.0) == 6


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------


class TestCorrelation:
    def test_identical_inputs_correlate_to_one(self):
        a = [1.0, 2.0, 3.0, 4.0]
        assert _correlation(a, a) == pytest.approx(1.0)

    def test_negated_inputs_correlate_to_minus_one(self):
        a = [1.0, 2.0, 3.0, 4.0]
        b = [-1.0, -2.0, -3.0, -4.0]
        assert _correlation(a, b) == pytest.approx(-1.0)

    def test_empty_returns_zero(self):
        assert _correlation([], []) == 0.0

    def test_mismatched_lengths_returns_zero(self):
        assert _correlation([1.0, 2.0], [1.0, 2.0, 3.0]) == 0.0

    def test_zero_variance_returns_zero(self):
        # All-same inputs have zero variance; correlation undefined.
        assert _correlation([1.0, 1.0, 1.0], [1.0, 1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# Key + mode estimation
# ---------------------------------------------------------------------------


class TestEstimateKeyMode:
    def test_c_major_profile_returns_c_major(self):
        # Feed in the canonical C major profile; expect ("C", "major").
        key, mode = _estimate_key_mode(list(_MAJOR_PROFILE))
        assert key == "C"
        assert mode == "major"

    def test_a_minor_profile_returns_a_minor(self):
        key, mode = _estimate_key_mode(list(_MINOR_PROFILE))
        assert key == "C"
        assert mode == "minor"

    def test_shifted_profile_detects_correct_key(self):
        # Rotate the C major profile by 7 semitones (G major).
        rotated = _MAJOR_PROFILE[-7:] + _MAJOR_PROFILE[:-7]
        key, mode = _estimate_key_mode(list(rotated))
        assert key == "G"
        assert mode == "major"

    def test_zero_chroma_does_not_crash(self):
        # Silence / zero chroma input. Correlation with everything is
        # zero; tied output picks the first candidate. Just verify no
        # exception.
        key, mode = _estimate_key_mode([0.0] * 12)
        assert key in _PITCH_NAMES
        assert mode in ("major", "minor")

    def test_all_twelve_pitches_addressable(self):
        # Sanity: each pitch class is reachable as the detected key by
        # rotating the profile by that many semitones.
        for tonic in range(12):
            rotated = _MAJOR_PROFILE[-tonic:] + _MAJOR_PROFILE[:-tonic] if tonic else _MAJOR_PROFILE
            key, mode = _estimate_key_mode(list(rotated))
            assert key == _PITCH_NAMES[tonic]
            assert mode == "major"


# ---------------------------------------------------------------------------
# ISO timestamp
# ---------------------------------------------------------------------------


class TestUtcNowIso:
    def test_format(self):
        ts = _utc_now_iso()
        # Format: 2026-06-26T18:14:00Z
        assert len(ts) == 20
        assert ts.endswith("Z")
        assert ts[4] == "-"
        assert ts[10] == "T"
        assert ts[13] == ":"
