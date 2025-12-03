"""DataUpdateCoordinator for Dominion Energy integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import logging

from dompower import (
    BillForecast,
    DompowerClient,
    IntervalUsageData,
    TokenExpiredError,
    InvalidAuthError,
    CannotConnectError,
    ApiError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_COST_MODE,
    CONF_FIXED_RATE,
    CONF_METER_NUMBER,
    CONF_OFF_PEAK_RATE,
    CONF_PEAK_END_HOUR,
    CONF_PEAK_RATE,
    CONF_PEAK_START_HOUR,
    CONF_REFRESH_TOKEN,
    COST_MODE_API,
    COST_MODE_TOU,
    DEFAULT_FIXED_RATE,
    DEFAULT_OFF_PEAK_RATE,
    DEFAULT_PEAK_END_HOUR,
    DEFAULT_PEAK_RATE,
    DEFAULT_PEAK_START_HOUR,
    DOMAIN,
    UPDATE_INTERVAL_MINUTES,
)


_LOGGER = logging.getLogger(__name__)

type DominionEnergyConfigEntry = ConfigEntry[DominionEnergyCoordinator]


@dataclass
class DominionEnergyData:
    """Data returned by the coordinator."""

    intervals: list[IntervalUsageData]
    latest_interval: IntervalUsageData | None
    daily_total: float
    monthly_total: float
    daily_cost: float
    monthly_cost: float
    bill_forecast: BillForecast | None

    @property
    def latest_usage(self) -> float | None:
        """Get the latest interval usage value."""
        return self.latest_interval.consumption if self.latest_interval else None


class DominionEnergyCoordinator(DataUpdateCoordinator[DominionEnergyData]):
    """Coordinator to manage fetching Dominion Energy data."""

    config_entry: DominionEnergyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: DominionEnergyConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )
        self._client: DompowerClient | None = None

    def _token_update_callback(self, access_token: str, refresh_token: str) -> None:
        """Handle token updates from the client."""
        new_data = {
            **self.config_entry.data,
            CONF_ACCESS_TOKEN: access_token,
            CONF_REFRESH_TOKEN: refresh_token,
        }
        self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
        _LOGGER.debug("Tokens updated and persisted")

    async def _async_setup(self) -> None:
        """Set up the coordinator (called once on first refresh)."""
        session = async_get_clientsession(self.hass)
        self._client = DompowerClient(
            session,
            access_token=self.config_entry.data[CONF_ACCESS_TOKEN],
            refresh_token=self.config_entry.data[CONF_REFRESH_TOKEN],
            token_update_callback=self._token_update_callback,
        )

    async def _async_update_data(self) -> DominionEnergyData:
        """Fetch data from the API."""
        if self._client is None:
            await self._async_setup()

        assert self._client is not None

        account_number = self.config_entry.data[CONF_ACCOUNT_NUMBER]
        meter_number = self.config_entry.data[CONF_METER_NUMBER]

        today = date.today()
        start_of_month = today.replace(day=1)

        try:
            # Fetch interval data for today (30-min intervals)
            intervals = await self._client.async_get_interval_usage(
                account_number=account_number,
                meter_number=meter_number,
                start_date=today,
                end_date=today,
            )

            # Calculate daily total from intervals
            daily_total = sum(i.consumption for i in intervals)

            # For monthly, fetch from start of month
            if today.day > 1:
                monthly_intervals = await self._client.async_get_interval_usage(
                    account_number=account_number,
                    meter_number=meter_number,
                    start_date=start_of_month,
                    end_date=today,
                )
                monthly_total = sum(i.consumption for i in monthly_intervals)
            else:
                monthly_intervals = intervals
                monthly_total = daily_total

            # Fetch bill forecast for cost calculation
            try:
                bill_forecast = await self._client.async_get_bill_forecast(
                    account_number=account_number,
                )
            except ApiError as err:
                _LOGGER.warning("Could not fetch bill forecast: %s", err)
                bill_forecast = None

            # Calculate costs
            daily_cost = self._calculate_cost(intervals, bill_forecast)
            monthly_cost = self._calculate_cost(monthly_intervals, bill_forecast)

            latest = intervals[-1] if intervals else None

            return DominionEnergyData(
                intervals=intervals,
                latest_interval=latest,
                daily_total=daily_total,
                monthly_total=monthly_total,
                daily_cost=daily_cost,
                monthly_cost=monthly_cost,
                bill_forecast=bill_forecast,
            )

        except (TokenExpiredError, InvalidAuthError) as err:
            raise ConfigEntryAuthFailed(
                "Authentication failed - please re-authenticate"
            ) from err
        except CannotConnectError as err:
            raise UpdateFailed(f"Cannot connect to Dominion Energy API: {err}") from err
        except ApiError as err:
            raise UpdateFailed(f"API error: {err}") from err

    def _calculate_cost(
        self,
        intervals: list[IntervalUsageData],
        bill_forecast: BillForecast | None,
    ) -> float:
        """Calculate cost based on configured mode."""
        if not intervals:
            return 0.0

        total_kwh = sum(i.consumption for i in intervals)
        options = self.config_entry.options
        cost_mode = options.get(CONF_COST_MODE, COST_MODE_API)

        if cost_mode == COST_MODE_API and bill_forecast:
            # Derive rate from last bill: charges / usage
            rate = bill_forecast.derived_rate
            if rate:
                return round(total_kwh * rate, 2)
            # Fallback to fixed if no derived rate available
            return round(
                total_kwh * options.get(CONF_FIXED_RATE, DEFAULT_FIXED_RATE), 2
            )

        elif cost_mode == COST_MODE_TOU:
            # Time-of-use calculation
            cost = 0.0
            peak_start = options.get(CONF_PEAK_START_HOUR, DEFAULT_PEAK_START_HOUR)
            peak_end = options.get(CONF_PEAK_END_HOUR, DEFAULT_PEAK_END_HOUR)
            peak_rate = options.get(CONF_PEAK_RATE, DEFAULT_PEAK_RATE)
            off_peak_rate = options.get(CONF_OFF_PEAK_RATE, DEFAULT_OFF_PEAK_RATE)

            for interval in intervals:
                hour = interval.timestamp.hour
                if peak_start <= hour < peak_end:
                    cost += interval.consumption * peak_rate
                else:
                    cost += interval.consumption * off_peak_rate
            return round(cost, 2)

        else:
            # Fixed rate
            fixed_rate = options.get(CONF_FIXED_RATE, DEFAULT_FIXED_RATE)
            return round(total_kwh * fixed_rate, 2)
