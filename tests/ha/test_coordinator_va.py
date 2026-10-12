"""Regression tests for Virginia/North Carolina entries after the SC changes."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

from dompower import (
    ApiError,
    BillForecast,
    BillPeriodData,
    DompowerClient,
    IntervalUsageData,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.dominion_energy.const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_METER_NUMBER,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)
from custom_components.dominion_energy.coordinator import DominionEnergyCoordinator

TZ = ZoneInfo("America/New_York")
COORDINATOR = "custom_components.dominion_energy.coordinator"


CONSUMPTION_ID = f"{DOMAIN}:123_energy_consumption"
GENERATION_ID = f"{DOMAIN}:123_energy_generation"


def _fake_usage(
    account_number,
    meter_number,
    start_date,
    end_date,
    solar=False,
    partial_day=None,
):
    """Return 30-minute intervals. partial_day has data only until 12:00.

    The intervals step through real time, so DST days have 46 or 50 intervals.
    """
    intervals = []
    day = start_date
    while day <= end_date:
        start = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(UTC)
        next_day = day + timedelta(days=1)
        end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=TZ)
        count = int((end.astimezone(UTC) - start).total_seconds() // 1800)
        for i in range(24 if day == partial_day else count):
            # astimezone sets fold=1 for the second 01:00 hour on fall-back day
            ts = (start + timedelta(minutes=30 * i)).astimezone(TZ)
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


def _usage_side_effect(**options):
    return lambda **kw: _fake_usage(**kw, **options)


def _mock_client(solar: bool = False) -> MagicMock:
    client = MagicMock(spec=DompowerClient)
    client.async_get_interval_usage = AsyncMock(
        side_effect=_usage_side_effect(solar=solar)
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


async def _setup_with_recorder(
    hass: HomeAssistant, entry: MockConfigEntry, client: MagicMock
) -> None:
    """Set up the entry and keep the statistics in the test recorder."""
    with patch(f"{COORDINATOR}.DompowerClient", return_value=client):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def _last_stat(hass: HomeAssistant, stat_id: str) -> tuple[datetime, float]:
    """Return the local start time and sum of the last statistic."""
    stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    row = stats[stat_id][0]
    start = datetime.fromtimestamp(row["start"], tz=TZ)
    return start, round(row["sum"], 3)


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
        if c.args[1]["statistic_id"] == GENERATION_ID
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
    # Statistics for consumption, cost, and generation use one download
    backfill_downloads = [
        c
        for c in entry.runtime_data._client.async_get_interval_usage.call_args_list
        if c.kwargs["start_date"] == date(2026, 6, 16)
    ]
    assert len(backfill_downloads) == 1


async def test_va_generation_continues_incrementally(
    hass: HomeAssistant, freezer
) -> None:
    """The next day adds to the generation sum, with one statistics download."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()
    client = _mock_client(solar=True)
    await _setup_with_recorder(hass, entry, client)

    # 60 days of backfill, Jun 16 to Aug 14
    assert await _last_stat(hass, GENERATION_ID) == (
        datetime(2026, 8, 14, 23, tzinfo=TZ),
        round(60 * 2.4, 3),
    )

    freezer.move_to("2026-08-16 12:00:00-04:00")
    client.async_get_interval_usage.reset_mock()
    await _refresh(hass, entry)

    assert await _last_stat(hass, GENERATION_ID) == (
        datetime(2026, 8, 15, 23, tzinfo=TZ),
        round(61 * 2.4, 3),
    )
    assert await _last_stat(hass, CONSUMPTION_ID) == (
        datetime(2026, 8, 15, 23, tzinfo=TZ),
        round(61 * 24.0, 3),
    )
    # Yesterday, month to date, and one download for all statistics
    assert client.async_get_interval_usage.await_count == 3


async def test_va_partial_day_is_replaced_for_generation(
    hass: HomeAssistant, freezer
) -> None:
    """A partial day is fetched again, and its energy is not counted twice."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()
    client = _mock_client()
    client.async_get_interval_usage.side_effect = _usage_side_effect(
        solar=True, partial_day=date(2026, 8, 14)
    )
    await _setup_with_recorder(hass, entry, client)

    start, _ = await _last_stat(hass, GENERATION_ID)
    assert start == datetime(2026, 8, 14, 11, tzinfo=TZ)

    # Dominion publishes the rest of Aug 14
    client.async_get_interval_usage.side_effect = _usage_side_effect(solar=True)
    freezer.move_to("2026-08-15 13:00:00-04:00")
    await _refresh(hass, entry)

    assert await _last_stat(hass, GENERATION_ID) == (
        datetime(2026, 8, 14, 23, tzinfo=TZ),
        round(60 * 2.4, 3),
    )
    assert await _last_stat(hass, CONSUMPTION_ID) == (
        datetime(2026, 8, 14, 23, tzinfo=TZ),
        round(60 * 24.0, 3),
    )


async def test_va_upgrade_backfills_generation(hass: HomeAssistant, freezer) -> None:
    """An entry without a generation statistic gets one when generation starts."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()
    client = _mock_client(solar=False)
    await _setup_with_recorder(hass, entry, client)

    stats = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, GENERATION_ID, True, {"sum"}
    )
    assert not stats

    # Solar is installed. The next update backfills generation only.
    client.async_get_interval_usage.side_effect = _usage_side_effect(solar=True)
    freezer.move_to("2026-08-16 12:00:00-04:00")
    await _refresh(hass, entry)

    # Jun 17 to Aug 15
    assert await _last_stat(hass, GENERATION_ID) == (
        datetime(2026, 8, 15, 23, tzinfo=TZ),
        round(60 * 2.4, 3),
    )

    # The next update continues consumption, and generation keeps its sum
    await _refresh(hass, entry)

    assert await _last_stat(hass, CONSUMPTION_ID) == (
        datetime(2026, 8, 15, 23, tzinfo=TZ),
        round(61 * 24.0, 3),
    )
    assert await _last_stat(hass, GENERATION_ID) == (
        datetime(2026, 8, 15, 23, tzinfo=TZ),
        round(60 * 2.4, 3),
    )


def test_filter_keeps_solar_day_without_grid_energy() -> None:
    """A day with generation but no consumption is not removed."""
    solar_day = [
        IntervalUsageData(
            timestamp=datetime(2026, 8, 14, tzinfo=TZ) + timedelta(minutes=30 * i),
            consumption=0.0,
            generation=0.2 if 20 <= i < 32 else 0.0,
        )
        for i in range(48)
    ]
    empty_day = [
        IntervalUsageData(
            timestamp=datetime(2026, 8, 15, tzinfo=TZ) + timedelta(minutes=30 * i),
            consumption=0.0,
        )
        for i in range(48)
    ]

    kept = DominionEnergyCoordinator._filter_incomplete_days(solar_day + empty_day)

    assert kept == solar_day


async def test_va_failed_generation_backfill_does_not_block_consumption(
    hass: HomeAssistant, freezer
) -> None:
    """Consumption and cost still update when the generation backfill fails."""
    freezer.move_to("2026-08-15 12:00:00-04:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()
    client = _mock_client(solar=False)
    await _setup_with_recorder(hass, entry, client)

    solar_usage = _usage_side_effect(solar=True)

    def backfill_fails(**kw):
        if kw["start_date"] == date(2026, 6, 17):
            raise ApiError("backfill download failed")
        return solar_usage(**kw)

    client.async_get_interval_usage.side_effect = backfill_fails
    freezer.move_to("2026-08-16 12:00:00-04:00")
    await _refresh(hass, entry)

    assert await _last_stat(hass, CONSUMPTION_ID) == (
        datetime(2026, 8, 15, 23, tzinfo=TZ),
        round(61 * 24.0, 3),
    )


async def test_dst_fall_back_day_has_25_hours(hass: HomeAssistant, freezer) -> None:
    """The two 01:00 hours on the fall-back day get separate statistics."""
    freezer.move_to("2026-11-02 12:00:00-05:00")
    await hass.config.async_set_time_zone("America/New_York")
    entry = _entry()
    await _setup_with_recorder(hass, entry, _mock_client())

    stats = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 11, 1, tzinfo=TZ),
        datetime(2026, 11, 2, tzinfo=TZ),
        {CONSUMPTION_ID},
        "hour",
        None,
        {"state"},
    )
    states = [row["state"] for row in stats[CONSUMPTION_ID]]
    assert len(states) == 25
    assert all(state == 1.0 for state in states)
