"""DataUpdateCoordinator for Dominion Energy integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging

from dompower import (
    ApiError,
    BillForecast,
    CannotConnectError,
    DompowerClient,
    GigyaAuthenticator,
    IntervalUsageData,
    InvalidAuthError,
    InvalidCredentialsError,
    TFARequiredError,
    TokenExpiredError,
)

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    BACKFILL_DAYS,
    CONF_ACCESS_TOKEN,
    CONF_ACCOUNT_NUMBER,
    CONF_COOKIES,
    CONF_COST_MODE,
    CONF_FIXED_RATE,
    CONF_METER_NUMBER,
    CONF_OFF_PEAK_RATE,
    CONF_PASSWORD,
    CONF_PEAK_END_HOUR,
    CONF_PEAK_RATE,
    CONF_PEAK_START_HOUR,
    CONF_REFRESH_TOKEN,
    CONF_USERNAME,
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
    # Date tracking for delayed data
    data_date: date | None  # Which day the daily data represents (yesterday)
    month_start_date: date | None  # Start of the month range
    month_end_date: date | None  # End of month range (last complete day)

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

    async def _async_attempt_reauth(self) -> bool:
        """Attempt to re-authenticate using stored credentials.

        Returns True if successful, False if manual reauth needed.
        """
        username = self.config_entry.data.get(CONF_USERNAME)
        password = self.config_entry.data.get(CONF_PASSWORD)
        existing_cookies = self.config_entry.data.get(CONF_COOKIES)

        if not username or not password:
            _LOGGER.warning("No stored credentials for auto-reauth")
            return False

        _LOGGER.info("Attempting automatic re-authentication for %s", username)
        session = async_get_clientsession(self.hass)

        try:
            # Use GigyaAuthenticator.async_login() without TFA callback
            # This will raise TFARequiredError if TFA is needed
            auth = GigyaAuthenticator(session)

            # Import existing cookies to potentially bypass TFA
            if existing_cookies:
                auth.import_cookies(existing_cookies)

            tokens = await auth.async_login(username, password, tfa_code_callback=None)

            # Export new cookies after successful login
            new_cookies = auth.export_cookies()

            # Update stored tokens and cookies in config entry
            new_data = {
                **self.config_entry.data,
                CONF_ACCESS_TOKEN: tokens.access_token,
                CONF_REFRESH_TOKEN: tokens.refresh_token,
                CONF_COOKIES: new_cookies,
            }
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=new_data
            )

            # Reinitialize client with new tokens
            self._client = DompowerClient(
                session,
                access_token=tokens.access_token,
                refresh_token=tokens.refresh_token,
                token_update_callback=self._token_update_callback,
            )

            _LOGGER.info("Successfully re-authenticated with stored credentials")
            return True

        except TFARequiredError:
            _LOGGER.info("TFA required during reauth - manual intervention needed")
            return False
        except InvalidCredentialsError as err:
            _LOGGER.warning("Auto-reauth failed - credentials invalid: %s", err)
            return False
        except CannotConnectError as err:
            _LOGGER.warning("Auto-reauth failed - connection error: %s", err)
            return False
        except Exception as err:
            _LOGGER.warning("Auto-reauth failed unexpectedly: %s", err)
            return False

    async def _async_update_data(self) -> DominionEnergyData:
        """Fetch data from the API.

        Note: The Dominion Energy API only provides data for completed days,
        so we always fetch yesterday's data (the most recent complete day).
        """
        if self._client is None:
            await self._async_setup()

        assert self._client is not None

        account_number = self.config_entry.data[CONF_ACCOUNT_NUMBER]
        meter_number = self.config_entry.data[CONF_METER_NUMBER]

        today = date.today()
        yesterday = today - timedelta(days=1)

        # Handle month boundary: determine which month's data we're working with
        if yesterday.month != today.month:
            # Yesterday was last day of previous month
            month_start = yesterday.replace(day=1)
        else:
            # Normal case: yesterday is in current month
            month_start = today.replace(day=1)

        try:
            # Fetch interval data for yesterday (last complete day)
            intervals = await self._client.async_get_interval_usage(
                account_number=account_number,
                meter_number=meter_number,
                start_date=yesterday,
                end_date=yesterday,
            )

            # Calculate daily total from intervals
            daily_total = sum(i.consumption for i in intervals)

            # For monthly, fetch from start of month to yesterday
            if month_start < yesterday:
                monthly_intervals = await self._client.async_get_interval_usage(
                    account_number=account_number,
                    meter_number=meter_number,
                    start_date=month_start,
                    end_date=yesterday,
                )
                monthly_total = sum(i.consumption for i in monthly_intervals)
            else:
                # First day of month or same day
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

            # Insert/update external statistics for Energy Dashboard
            await self._insert_statistics(account_number, meter_number, yesterday)

            return DominionEnergyData(
                intervals=intervals,
                latest_interval=latest,
                daily_total=daily_total,
                monthly_total=monthly_total,
                daily_cost=daily_cost,
                monthly_cost=monthly_cost,
                bill_forecast=bill_forecast,
                data_date=yesterday,
                month_start_date=month_start,
                month_end_date=yesterday,
            )

        except TokenExpiredError as err:
            _LOGGER.info("Refresh token expired, attempting auto-reauth")
            if await self._async_attempt_reauth():
                # Retry the update with new tokens
                return await self._async_update_data()
            raise ConfigEntryAuthFailed(
                "Authentication failed - please re-authenticate"
            ) from err
        except InvalidAuthError as err:
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

    async def _insert_statistics(
        self,
        account_number: str,
        meter_number: str,
        data_date: date,
    ) -> None:
        """Insert or update external statistics for Energy Dashboard integration.

        Statistics are stored with hourly granularity, aggregated from 30-minute
        interval data. On first setup, backfills BACKFILL_DAYS days of history.
        """
        stat_id = f"{DOMAIN}:{account_number}_energy_consumption"

        # Check if we have existing statistics
        last_stat = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, stat_id, True, {"sum"}
        )

        if not last_stat.get(stat_id):
            # First time - backfill historical data
            _LOGGER.info(
                "First statistics update for %s - backfilling %d days of data",
                account_number,
                BACKFILL_DAYS,
            )
            await self._backfill_statistics(account_number, meter_number, stat_id)
        else:
            # Incremental update
            await self._update_statistics(
                account_number, meter_number, stat_id, last_stat, data_date
            )

    async def _backfill_statistics(
        self,
        account_number: str,
        meter_number: str,
        stat_id: str,
    ) -> None:
        """Backfill historical statistics for initial setup."""
        assert self._client is not None

        today = date.today()
        end_date = today - timedelta(days=1)  # Yesterday
        start_date = today - timedelta(days=BACKFILL_DAYS)

        _LOGGER.debug("Backfilling statistics from %s to %s", start_date, end_date)

        try:
            intervals = await self._client.async_get_interval_usage(
                account_number=account_number,
                meter_number=meter_number,
                start_date=start_date,
                end_date=end_date,
            )
        except ApiError as err:
            _LOGGER.warning("Could not fetch backfill data: %s", err)
            return

        if not intervals:
            _LOGGER.warning("No interval data available for backfill")
            return

        # Group intervals by hour for hourly statistics
        hourly_data: dict[datetime, float] = {}
        for interval in intervals:
            # Normalize to hour start
            hour_start = interval.timestamp.replace(minute=0, second=0, microsecond=0)
            if hour_start not in hourly_data:
                hourly_data[hour_start] = 0.0
            hourly_data[hour_start] += interval.consumption

        # Build statistics with cumulative sum
        consumption_statistics: list[StatisticData] = []
        consumption_sum = 0.0

        for hour_start in sorted(hourly_data.keys()):
            consumption = hourly_data[hour_start]
            consumption_sum += consumption
            # Ensure timezone-aware datetime
            aware_dt = dt_util.as_utc(hour_start)
            consumption_statistics.append(
                StatisticData(start=aware_dt, state=consumption, sum=consumption_sum)
            )

        if not consumption_statistics:
            _LOGGER.warning("No statistics to insert after processing intervals")
            return

        # Create metadata for the statistic
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Dominion Energy {account_number} consumption",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )

        _LOGGER.debug(
            "Adding %d hourly statistics for %s", len(consumption_statistics), stat_id
        )
        async_add_external_statistics(self.hass, metadata, consumption_statistics)

    async def _update_statistics(
        self,
        account_number: str,
        meter_number: str,
        stat_id: str,
        last_stat: dict,
        data_date: date,
    ) -> None:
        """Update statistics with new data since last recorded statistic."""
        assert self._client is not None

        # Get the last recorded statistic time and sum
        last_stat_data = last_stat[stat_id][0]
        last_stat_start = last_stat_data["start"]
        current_sum = float(last_stat_data.get("sum", 0))

        # Convert to datetime for comparison
        if isinstance(last_stat_start, (int, float)):
            last_stat_dt = datetime.fromtimestamp(last_stat_start, tz=dt_util.UTC)
        else:
            last_stat_dt = last_stat_start

        # Convert to local timezone for date comparison since data_date is local
        # (The timestamps are stored as UTC but actually represent local times
        # due to how the dompower library parses them)
        local_tz = dt_util.get_default_time_zone()
        last_stat_local = last_stat_dt.astimezone(local_tz)
        last_stat_date = last_stat_local.date()

        # Check if we need to fetch new data
        if last_stat_date >= data_date:
            _LOGGER.info(
                "Statistics already up to date: last_stat_date=%s >= data_date=%s",
                last_stat_date,
                data_date,
            )
            return

        # Fetch data from day after last stat to data_date
        start_date = last_stat_date + timedelta(days=1)
        _LOGGER.info(
            "Fetching statistics update from %s to %s (current_sum=%.3f)",
            start_date,
            data_date,
            current_sum,
        )

        try:
            intervals = await self._client.async_get_interval_usage(
                account_number=account_number,
                meter_number=meter_number,
                start_date=start_date,
                end_date=data_date,
            )
        except ApiError as err:
            _LOGGER.warning("Could not fetch statistics update data: %s", err)
            return

        if not intervals:
            _LOGGER.debug("No new interval data for statistics update")
            return

        # Group intervals by hour
        hourly_data: dict[datetime, float] = {}
        for interval in intervals:
            hour_start = interval.timestamp.replace(minute=0, second=0, microsecond=0)
            if hour_start not in hourly_data:
                hourly_data[hour_start] = 0.0
            hourly_data[hour_start] += interval.consumption

        # Build new statistics
        consumption_statistics: list[StatisticData] = []

        for hour_start in sorted(hourly_data.keys()):
            consumption = hourly_data[hour_start]
            current_sum += consumption
            aware_dt = dt_util.as_utc(hour_start)
            consumption_statistics.append(
                StatisticData(start=aware_dt, state=consumption, sum=current_sum)
            )

        if not consumption_statistics:
            return

        # Create metadata
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"Dominion Energy {account_number} consumption",
            source=DOMAIN,
            statistic_id=stat_id,
            unit_class="energy",
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        )

        _LOGGER.info(
            "Adding %d new hourly statistics for %s (sum=%.3f)",
            len(consumption_statistics),
            stat_id,
            current_sum,
        )
        async_add_external_statistics(self.hass, metadata, consumption_statistics)
