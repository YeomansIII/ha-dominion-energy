"""Regression tests for Virginia/North Carolina entries after the SC changes."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from dompower import BillForecast, BillPeriodData, DompowerClient, IntervalUsageData
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dominion_energy.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_METER_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

TZ = ZoneInfo("America/New_York")
COORDINATOR = "custom_components.dominion_energy.coordinator"


def _fake_usage(account_number, meter_number, start_date, end_date):
    intervals = []
    day = start_date
    while day <= end_date:
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
        intervals.extend(
            IntervalUsageData(
                timestamp=midnight + timedelta(minutes=30 * i), consumption=0.5
            )
            for i in range(48)
        )
        day += timedelta(days=1)
    return intervals


async def test_entry_without_region_uses_dompower_client(
    hass: HomeAssistant, freezer
) -> None:
    """An entry from before SC support still works as VA/NC."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="123",
        data={
            CONF_ACCESS_TOKEN: "a",
            CONF_REFRESH_TOKEN: "r",
            CONF_ACCOUNT_NUMBER: "123",
            CONF_METER_NUMBER: "456",
        },
    )
    client = MagicMock(spec=DompowerClient)
    client.async_get_interval_usage = AsyncMock(
        side_effect=lambda **kw: _fake_usage(**kw)
    )
    client.async_get_bill_forecast = AsyncMock(
        return_value=BillForecast(
            last_bill=BillPeriodData(charges=100.0, usage=1000.0),
            current_period_start=date(2026, 8, 1),
            current_period_end=date(2026, 8, 31),
            current_usage_kwh=300.0,
            is_tou=False,
        )
    )
    add_stats = MagicMock()

    with (
        patch(f"{COORDINATOR}.DompowerClient", return_value=client),
        patch(f"{COORDINATOR}.async_add_external_statistics", add_stats),
    ):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data.data
    assert data.daily_total == 24.0
    assert data.derived_rate == 0.1
    assert data.daily_cost == 2.4
    stat_ids = {call.args[1]["statistic_id"] for call in add_stats.call_args_list}
    assert stat_ids == {f"{DOMAIN}:123_energy_consumption", f"{DOMAIN}:123_energy_cost"}
    assert (
        add_stats.call_args_list[0].args[1]["name"] == "Dominion Energy 123 consumption"
    )

    assert (
        hass.states.get("sensor.dominion_energy_123_last_bill_charges").state == "100.0"
    )
    assert hass.states.get("sensor.dominion_energy_123_effective_rate").state == "0.1"
    assert (
        hass.states.get("sensor.dominion_energy_123_billing_period_start").state
        == "2026-08-01"
    )
    assert hass.states.get("sensor.dominion_energy_123_forecasted_bill") is None
