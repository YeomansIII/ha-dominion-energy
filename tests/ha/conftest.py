"""Fixtures for the Home Assistant tests.

These tests need the "ha-test" extra:

    uv run --extra ha-test pytest tests/ha
"""

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Let Home Assistant load custom_components/dominion_energy.

    The integration depends on the recorder, so a test recorder is set up too.
    """
    return
