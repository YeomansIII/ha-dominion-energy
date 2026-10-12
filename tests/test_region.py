"""Tests for the region helpers (no Home Assistant needed)."""

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

# Load region.py and const.py as a package without running __init__.py,
# which imports Home Assistant.
_PKG_DIR = (
    Path(__file__).resolve().parent.parent / "custom_components" / "dominion_energy"
)
_PKG_NAME = "_dominion_energy_pure"
if _PKG_NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PKG_NAME, _PKG_DIR / "__init__.py", submodule_search_locations=[str(_PKG_DIR)]
    )
    assert _spec is not None
    sys.modules[_PKG_NAME] = importlib.util.module_from_spec(_spec)

region = importlib.import_module(f"{_PKG_NAME}.region")
const = importlib.import_module(f"{_PKG_NAME}.const")


class TestGetRegion:
    """Tests for get_region and is_sc."""

    def test_old_entries_are_va_nc(self):
        assert region.get_region({}) == const.REGION_VA_NC
        assert not region.is_sc({})

    def test_sc_entry(self):
        data = {const.CONF_REGION: const.REGION_SC}
        assert region.get_region(data) == const.REGION_SC
        assert region.is_sc(data)


class TestScAccountKey:
    """Tests for sc_account_key."""

    def test_uuid(self):
        key = region.sc_account_key("1A2B3C4D-aaaa-BBBB-cccc-0123456789AB")
        assert key == "sc_1a2b3c4d_aaaa_bbbb_cccc_0123456789ab"

    def test_only_safe_characters(self):
        key = region.sc_account_key("  weird/ID==  ")
        assert key == "sc_weird_id"
        assert all(c.islower() or c.isdigit() or c == "_" for c in key)


class TestScDerivedRate:
    """Tests for sc_derived_rate."""

    def test_electric_only(self):
        assert region.sc_derived_rate(50.0, 400.0, has_gas=False) == pytest.approx(
            0.125
        )

    def test_gas_account_has_no_rate(self):
        assert region.sc_derived_rate(50.0, 400.0, has_gas=True) is None

    def test_no_usage(self):
        assert region.sc_derived_rate(50.0, 0.0, has_gas=False) is None

    def test_no_cost(self):
        assert region.sc_derived_rate(0.0, 400.0, has_gas=False) is None
