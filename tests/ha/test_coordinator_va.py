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


def _fake_usage(account_number, meter_number, start_date, end_date, solar=False):
    intervals = []
    day = start_date
    while day <= end_date:
        midnight = datetime(day.year, day.month, day.day, tzinfo=TZ)
        for i in range(48):
            ts = midnight + timedelta(minutes=30 * i)
            intervals.append(
                IntervalUsageData(
                    timestamp=ts,
                    consumption=0.5,
                    generation=0.2 if solar and 10 <= ts.hour < 16 else 0.0,
                )
            )
        day += timedelta(days=1)
    return intervals


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="123",
        data={
            CONF_ACCESS_TOKEN: "a",
            CONF_REFRESH_TOKEN: "r",
            CONF_ACCOUNT_NUMBER: "123",
            CONF_METER_NUMBER: "456",
        },
    )


def _mock_client(solar: bool = False) -> MagicMock:
    client = MagicMock(spec=DompowerClient)
    client.async_get_interval_usage = AsyncMock(
        side_effect=lambda **kw: _fake_usage(**kw, solar=solar)
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
    return client


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock):
    add_stats = MagicMock()
    with (
        patch(f"{COORDINATOR}.DompowerClient", return_value=client),
        patch(f"{COORDINATOR}.async_add_external_statistics", add_stats),
    ):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return add_stats


async def test_entry_without_region_uses_dompower_client(
    hass: HomeAssistant, freezer
) -> None:
    """An entry from before SC support still works as VA/NC."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()

    add_stats = await _setup(hass, entry, _mock_client())

    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data.data
    assert data.daily_total == 24.0
    assert data.derived_rate == 0.1
    assert data.daily_cost == 2.4
    # No solar, so there is no generation statistic
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


async def test_va_solar_account_gets_generation(hass: HomeAssistant, freezer) -> None:
    """A VA/NC account with solar gets generation sensors and a statistic."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()

    add_stats = await _setup(hass, entry, _mock_client(solar=True))

    data = entry.runtime_data.data
    assert round(data.daily_generation, 3) == 2.4
    assert round(data.monthly_generation, 3) == 14 * 2.4
    # Generation does not change consumption or cost
    assert data.daily_total == 24.0
    assert data.daily_cost == 2.4

    gen_calls = [
        c
        for c in add_stats.call_args_list
        if c.args[1]["statistic_id"] == f"{DOMAIN}:123_energy_generation"
    ]
    assert len(gen_calls) == 1
    assert gen_calls[0].args[1]["name"] == "Dominion Energy 123 generation"
    gen_stats = gen_calls[0].args[2]
    assert round(gen_stats[-1]["sum"], 3) == round(
        sum(s["state"] for s in gen_stats), 3
    )
    assert round(sum(s["state"] for s in gen_stats), 3) > 0

    assert (
        hass.states.get("sensor.dominion_energy_123_yesterday_s_generation").state
        == "2.4"
    )
    assert (
        hass.states.get("sensor.dominion_energy_123_latest_interval_generation")
        is not None
    )
