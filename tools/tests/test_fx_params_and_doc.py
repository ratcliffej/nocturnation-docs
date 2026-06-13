"""Tests for the param-unit converter and the doc generator (B4)."""

import re
import sys
from pathlib import Path

import pytest

from nocturnation_orchestrator.fx import library  # noqa: F401  side-effects
from nocturnation_orchestrator.fx.params import (
    VALID_UNITS, convert_to_u8, validate_params_attr,
)
from nocturnation_orchestrator.fx.registry import fx_registry


# ---------------------------------------------------------------------------
# Param converter
# ---------------------------------------------------------------------------

class TestConvertToU8:
    def test_u8_pass_through(self):
        assert convert_to_u8(0,   "u8") == 0
        assert convert_to_u8(128, "u8") == 128
        assert convert_to_u8(255, "u8") == 255

    def test_percent_scales(self):
        assert convert_to_u8(0,   "percent") == 0
        assert convert_to_u8(50,  "percent") == 128   # round(127.5)
        assert convert_to_u8(100, "percent") == 255

    def test_count_pass_through(self):
        assert convert_to_u8(4, "count") == 4
        assert convert_to_u8(9, "count") == 9

    def test_100ms_pass_through(self):
        assert convert_to_u8(80, "100ms") == 80

    def test_out_of_range_rejected(self):
        for unit in VALID_UNITS:
            top = 100 if unit == "percent" else 255
            with pytest.raises(ValueError):
                convert_to_u8(-1, unit)
            with pytest.raises(ValueError):
                convert_to_u8(top + 1, unit)

    def test_unknown_unit_rejected(self):
        with pytest.raises(ValueError):
            convert_to_u8(50, "wat")


# ---------------------------------------------------------------------------
# PARAMS attribute on every registered FX
# ---------------------------------------------------------------------------

class TestEveryFxHasValidParams:
    @pytest.mark.parametrize(
        "fx_id", sorted(fx_registry.all_ids()))
    def test_params_attr_well_formed(self, fx_id):
        cls = fx_registry.get(fx_id)
        validate_params_attr(cls)

    @pytest.mark.parametrize(
        "fx_id", sorted(fx_registry.all_ids()))
    def test_metadata_complete(self, fx_id):
        cls = fx_registry.get(fx_id)
        assert isinstance(cls.cue_name, str) and cls.cue_name
        assert isinstance(cls.description, str) and cls.description
        # cue_name must be snake_case (lowercase + underscores).
        assert re.match(r"^[a-z][a-z0-9_]*$", cls.cue_name), (
            "cue_name %r is not snake_case" % cls.cue_name
        )

    def test_cue_names_unique(self):
        names = [fx_registry.get(i).cue_name for i in fx_registry.all_ids()]
        assert len(set(names)) == len(names), "duplicate cue_name in registry"


# ---------------------------------------------------------------------------
# Doc generator
# ---------------------------------------------------------------------------

# Import the generator module by path so the test doesn't require a
# package install. The generator lives outside the orchestrator package.
_TOOLS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_TOOLS_DIR / "scripts"))
import gen_fx_library  # noqa: E402


class TestGenFxLibrary:
    def test_build_doc_runs_clean(self):
        doc = gen_fx_library.build_doc()
        assert isinstance(doc, str)
        assert len(doc) > 1000  # non-trivial content

    def test_doc_lists_every_registered_fx(self):
        doc = gen_fx_library.build_doc()
        for fx_id in fx_registry.all_ids():
            cls = fx_registry.get(fx_id)
            assert "### %s (id %d)" % (cls.name, fx_id) in doc, (
                "doc missing entry for %s" % cls.name
            )
            assert "`%s`" % cls.cue_name in doc

    def test_doc_documents_each_unit(self):
        doc = gen_fx_library.build_doc()
        for unit in VALID_UNITS:
            assert "`%s`" % unit in doc

    def test_doc_contains_cue_format_example(self):
        doc = gen_fx_library.build_doc()
        # Format example should mention the directives and a cue line.
        assert "@bpm" in doc
        assert "sparkle_on_beat" in doc
        assert "stop" in doc

    def test_md_table_alignment(self):
        rows = [
            ["Slot", "Name", "Unit", "Description"],
            ["0",    "r",    "u8",   "Pulse Red."],
            ["1",    "g",    "u8",   "Pulse Green."],
        ]
        table = gen_fx_library._md_table(rows)
        lines = table.splitlines()
        # Header + separator + 2 data rows.
        assert len(lines) == 4
        # All lines same length (padded for alignment).
        assert len(set(len(line) for line in lines)) == 1
