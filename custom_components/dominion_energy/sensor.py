"""Sensor platform for Dominion Energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ACCOUNT_NUMBER, CONF_SERVICE_ADDRESS, DOMAIN
from .coordinator import (
    DominionEnergyConfigEntry,
    DominionEnergyCoordinator,
    DominionEnergyData,
)

PARALLEL_UPDATES = 0  # Coordinator handles updates


@dataclass(frozen=True, kw_only=True)
class DominionEnergySensorDescription(SensorEntityDescription):  # type: ignore[override]
    """Describes a Dominion Energy sensor."""

    value_fn: Callable[[DominionEnergyData], float | str | date | None]


# Sensors for all regions
SENSORS: tuple[DominionEnergySensorDescription, ...] = (
    DominionEnergySensorDescription(
        key="latest_interval_usage",
        name="Latest interval usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # Note: Not using device_class=ENERGY because state_class=MEASUREMENT
        # is incompatible with energy device class (requires total/total_increasing)
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.latest_usage,
    ),
    DominionEnergySensorDescription(
        key="daily_usage",
        name="Yesterday's usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda data: data.daily_total,
    ),
    DominionEnergySensorDescription(
        key="monthly_usage",
        name="Current month usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda data: data.monthly_total,
    ),
    DominionEnergySensorDescription(
        key="latest_interval_generation",
        name="Latest interval generation",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        # Note: Not using device_class=ENERGY because state_class=MEASUREMENT
        # is incompatible with energy device class (requires total/total_increasing)
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.latest_generation,
    ),
    DominionEnergySensorDescription(
        key="daily_generation",
        name="Yesterday's generation",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda data: data.daily_generation,
    ),
    DominionEnergySensorDescription(
        key="monthly_generation",
        name="Current month generation",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda data: data.monthly_generation,
    ),
    DominionEnergySensorDescription(
        key="daily_cost",
        name="Yesterday's cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.daily_cost,
    ),
    DominionEnergySensorDescription(
        key="monthly_cost",
        name="Current month cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.monthly_cost,
    ),
    DominionEnergySensorDescription(
        key="effective_rate",
        name="Effective rate",
        native_unit_of_measurement="USD/kWh",
        device_class=None,  # No standard device class for rates
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data: data.derived_rate,
    ),
    DominionEnergySensorDescription(
        key="billing_period_start",
        name="Billing period start",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.billing_period_start,
    ),
    DominionEnergySensorDescription(
        key="billing_period_end",
        name="Billing period end",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.billing_period_end,
    ),
)

# Bill forecast sensors for Virginia and North Carolina
VA_NC_SENSORS: tuple[DominionEnergySensorDescription, ...] = (
    DominionEnergySensorDescription(
        key="last_bill_charges",
        name="Last bill charges",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: (
            data.bill_forecast.last_bill.charges if data.bill_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="last_bill_usage",
        name="Last bill usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=1,
        value_fn=lambda data: (
            data.bill_forecast.last_bill.usage if data.bill_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="current_period_usage",
        name="Current billing period usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda data: (
            data.bill_forecast.current_usage_kwh if data.bill_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="is_time_of_use",
        name="Time-of-use plan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            "Yes" if data.bill_forecast and data.bill_forecast.is_tou else "No"
        ),
    ),
)

# Bill forecast sensors for South Carolina. The SC bill values
# include electric and gas together.
SC_SENSORS: tuple[DominionEnergySensorDescription, ...] = (
    DominionEnergySensorDescription(
        key="bill_cost_to_date",
        name="Bill cost to date",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: (
            data.sc_forecast.cost_to_date if data.sc_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="forecasted_bill",
        name="Forecasted bill",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: (
            data.sc_forecast.forecasted_cost if data.sc_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="last_year_bill",
        name="Same period last year",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: (
            data.sc_forecast.last_year_cost if data.sc_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="bill_data_through",
        name="Bill data through",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.sc_forecast.data_through if data.sc_forecast else None
        ),
    ),
)

# Gas sensors for South Carolina accounts with a gas meter
SC_GAS_SENSORS: tuple[DominionEnergySensorDescription, ...] = (
    DominionEnergySensorDescription(
        key="daily_gas_usage",
        name="Yesterday's gas usage",
        native_unit_of_measurement=UnitOfVolume.CUBIC_FEET,
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda data: data.daily_gas,
    ),
    DominionEnergySensorDescription(
        key="monthly_gas_usage",
        name="Current month gas usage",
        native_unit_of_measurement=UnitOfVolume.CUBIC_FEET,
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda data: data.monthly_gas,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DominionEnergyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Dominion Energy sensors."""
    coordinator = entry.runtime_data
    account_number = entry.data[CONF_ACCOUNT_NUMBER]
    service_address = entry.data.get(CONF_SERVICE_ADDRESS)

    if coordinator.is_sc:
        descriptions = SENSORS + SC_SENSORS
        if coordinator.has_gas:
            descriptions += SC_GAS_SENSORS
        device_name = f"Dominion Energy SC {service_address or account_number}"
        configuration_url = "https://account.dominionenergysc.com"
    else:
        descriptions = SENSORS + VA_NC_SENSORS
        device_name = f"Dominion Energy {account_number}"
        configuration_url = "https://myaccount.dominionenergy.com"

    device_info = DeviceInfo(
        identifiers={(DOMAIN, account_number)},
        name=device_name,
        manufacturer="Dominion Energy",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=configuration_url,
    )

    # Add service address as model if available
    if service_address:
        device_info["model"] = service_address

    async_add_entities(
        DominionEnergySensor(
            coordinator=coordinator,
            description=description,
            device_info=device_info,
            account_number=account_number,
        )
        for description in descriptions
    )


class DominionEnergySensor(CoordinatorEntity[DominionEnergyCoordinator], SensorEntity):
    """Representation of a Dominion Energy sensor."""

    _attr_has_entity_name = True
    entity_description: DominionEnergySensorDescription

    def __init__(
        self,
        coordinator: DominionEnergyCoordinator,
        description: DominionEnergySensorDescription,
        device_info: DeviceInfo,
        account_number: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{account_number}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | str | date | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra state attributes including data_date.

        Adds data_date attribute to daily/interval sensors to indicate which
        day the data represents (since data is delayed by ~1 day).
        """
        if self.coordinator.data is None:
            return None

        attrs: dict[str, Any] = {}
        key = self.entity_description.key

        # Add data_date for daily and interval sensors
        if (
            key
            in (
                "daily_usage",
                "daily_cost",
                "latest_interval_usage",
                "daily_generation",
                "latest_interval_generation",
                "daily_gas_usage",
            )
            and self.coordinator.data.data_date
        ):
            attrs["data_date"] = self.coordinator.data.data_date.isoformat()

        # Add date range for monthly sensors
        if key in (
            "monthly_usage",
            "monthly_cost",
            "monthly_generation",
            "monthly_gas_usage",
        ):
            data = self.coordinator.data
            if data.month_start_date:
                attrs["month_start"] = data.month_start_date.isoformat()
            if data.month_end_date:
                attrs["month_end"] = data.month_end_date.isoformat()

        return attrs if attrs else None
