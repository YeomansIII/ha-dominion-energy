"""Coordinator tests for South Carolina accounts."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from dompower import (
    CannotConnectError,
    DominionSCClient,
    IntervalUsageData,
    InvalidAuthError,
    MeterType,
    SCBillForecast,
    TFARequiredError,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dominion_energy.const import (
    CONF_ACCOUNT_NUMBER,
    CONF_METER_TYPES,
    CONF_PASSWORD,
    CONF_REGION,
    CONF_SERVICE_ADDRESS,
    CONF_TFA_TOKEN,
    CONF_USERNAME,
    DOMAIN,
    REGION_SC,
)

SC_TZ = ZoneInfo("America/New_York")
ACCOUNT_KEY = "sc_abcd_1234"
COORDINATOR = "custom_components.dominion_energy.coordinator"


def _fake_usage(start: date, end: date, meter_type=MeterType.ELECTRIC):
    """Return 15-minute electric or hourly gas intervals for each day."""
    intervals = []
    day = start
    while day <= end:
        midnight = datetime(day.year, day.month, day.day, tzinfo=SC_TZ)
        if meter_type is MeterType.GAS:
            for hour in range(24):
                intervals.append(
                    IntervalUsageData(
                        timestamp=midnight + timedelta(hours=hour),
                        consumption=5.0,
                        unit="ft³",
                    )
                )
        else:
            for slot in range(96):
                ts = midnight + timedelta(minutes=15 * slot)
                intervals.append(
                    IntervalUsageData(
                        timestamp=ts,
                        consumption=0.25,
                        generation=0.1 if 10 <= ts.hour < 16 else 0.0,
                    )
                )
        day += timedelta(days=1)
    return intervals


def _entry(meter_types: list[str]) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=ACCOUNT_KEY,
        data={
            CONF_REGION: REGION_SC,
            CONF_USERNAME: "me",
            CONF_PASSWORD: "pw",
            CONF_TFA_TOKEN: "token",
            CONF_ACCOUNT_NUMBER: ACCOUNT_KEY,
            CONF_SERVICE_ADDRESS: "1 MAIN ST",
            CONF_METER_TYPES: meter_types,
        },
    )


def _mock_client() -> MagicMock:
    client = MagicMock(spec=DominionSCClient)
    client.async_login = AsyncMock()
    client.async_get_interval_usage = AsyncMock(side_effect=_fake_usage)
    client.async_get_bill_forecast = AsyncMock(
        return_value=SCBillForecast(
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 31),
            data_through=date(2026, 8, 14),
            cost_to_date=42.0,
            forecasted_cost=95.0,
            last_year_cost=None,
        )
    )
    return client


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock):
    add_stats = MagicMock()
    with (
        patch(f"{COORDINATOR}.DominionSCClient", return_value=client),
        patch(f"{COORDINATOR}.async_create_clientsession"),
        patch(f"{COORDINATOR}.async_add_external_statistics", add_stats),
    ):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return add_stats


async def test_sc_update_with_solar_and_gas(hass: HomeAssistant, freezer) -> None:
    """An SC update gives usage, generation, gas, bill data, and statistics."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry(["electric", "gas"])
    client = _mock_client()

    add_stats = await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.LOADED
    client.async_login.assert_awaited()
    data = entry.runtime_data.data
    assert data.daily_total == 24.0
    assert data.monthly_total == 14 * 24.0
    assert round(data.daily_generation, 3) == 2.4
    assert data.daily_gas == 120.0
    assert data.monthly_gas == 14 * 120.0
    assert data.sc_forecast.cost_to_date == 42.0
    # The SC cost includes gas, so there is no derived rate. Fixed rate is used.
    assert data.derived_rate is None
    assert data.daily_cost == round(24.0 * 0.12, 2)

    stat_ids = {call.args[1]["statistic_id"] for call in add_stats.call_args_list}
    assert stat_ids == {
        f"{DOMAIN}:{ACCOUNT_KEY}_energy_consumption",
        f"{DOMAIN}:{ACCOUNT_KEY}_energy_cost",
        f"{DOMAIN}:{ACCOUNT_KEY}_energy_generation",
        f"{DOMAIN}:{ACCOUNT_KEY}_gas_consumption",
    }
    gas_call = next(
        c
        for c in add_stats.call_args_list
        if c.args[1]["statistic_id"].endswith("gas_consumption")
    )
    assert gas_call.args[1]["unit_of_measurement"] == "ft³"

    assert (
        hass.states.get("sensor.dominion_energy_sc_1_main_st_forecasted_bill").state
        == "95.0"
    )
    assert (
        hass.states.get("sensor.dominion_energy_sc_1_main_st_yesterday_s_gas_usage")
        is not None
    )
    # VA/NC-only sensors are not created
    assert (
        hass.states.get("sensor.dominion_energy_sc_1_main_st_last_bill_charges") is None
    )


async def test_sc_electric_only_derives_rate(hass: HomeAssistant, freezer) -> None:
    """An electric-only SC account derives a rate from the bill forecast."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry(["electric"])
    client = _mock_client()

    await _setup(hass, entry, client)

    data = entry.runtime_data.data
    # $42 over 14 days of 24 kWh
    assert round(data.derived_rate, 4) == round(42.0 / (14 * 24.0), 4)
    assert data.daily_gas is None
    assert (
        hass.states.get("sensor.dominion_energy_sc_1_main_st_yesterday_s_gas_usage")
        is None
    )


async def test_sc_optional_download_errors_keep_entry_loaded(
    hass: HomeAssistant, freezer
) -> None:
    """Gas and bill forecast errors do not stop the electric update."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry(["electric", "gas"])
    client = _mock_client()
    client.async_get_bill_forecast.side_effect = CannotConnectError("503")

    def gas_fails(start, end, meter_type=MeterType.ELECTRIC):
        if meter_type is MeterType.GAS:
            raise InvalidAuthError("401 on gas download")
        return _fake_usage(start, end, meter_type)

    client.async_get_interval_usage.side_effect = gas_fails

    add_stats = await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.LOADED
    assert not hass.config_entries.flow.async_progress()
    data = entry.runtime_data.data
    assert data.daily_total == 24.0
    assert data.sc_forecast is None
    assert data.daily_gas is None
    stat_ids = {call.args[1]["statistic_id"] for call in add_stats.call_args_list}
    assert f"{DOMAIN}:{ACCOUNT_KEY}_energy_consumption" in stat_ids
    assert f"{DOMAIN}:{ACCOUNT_KEY}_gas_consumption" not in stat_ids


async def test_sc_rejected_tfa_token_starts_reauth(hass: HomeAssistant) -> None:
    """A rejected TFA token puts the entry into reauth."""
    entry = _entry(["electric"])
    client = _mock_client()
    client.async_login.side_effect = TFARequiredError("new TFA needed")

    await _setup(hass, entry, client)

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)
