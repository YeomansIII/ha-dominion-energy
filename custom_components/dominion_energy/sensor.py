"""Sensor platform for Dominion Energy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
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
class DominionEnergySensorDescription(SensorEntityDescription):
    """Describes a Dominion Energy sensor."""

    value_fn: Callable[[DominionEnergyData], float | str | date | None]


SENSORS: tuple[DominionEnergySensorDescription, ...] = (
    # Existing sensors
    DominionEnergySensorDescription(
        key="latest_interval_usage",
        translation_key="latest_interval_usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda data: data.latest_usage,
    ),
    DominionEnergySensorDescription(
        key="daily_usage",
        translation_key="daily_usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda data: data.daily_total,
    ),
    DominionEnergySensorDescription(
        key="monthly_usage",
        translation_key="monthly_usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda data: data.monthly_total,
    ),
    DominionEnergySensorDescription(
        key="daily_cost",
        translation_key="daily_cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.daily_cost,
    ),
    DominionEnergySensorDescription(
        key="monthly_cost",
        translation_key="monthly_cost",
        native_unit_of_measurement="USD",
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=lambda data: data.monthly_cost,
    ),
    # New bill forecast sensors - Primary
    DominionEnergySensorDescription(
        key="last_bill_charges",
        translation_key="last_bill_charges",
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
        translation_key="last_bill_usage",
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
        translation_key="current_period_usage",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=lambda data: (
            data.bill_forecast.current_usage_kwh if data.bill_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="effective_rate",
        translation_key="effective_rate",
        native_unit_of_measurement="USD/kWh",
        device_class=None,  # No standard device class for rates
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data: (
            data.bill_forecast.derived_rate if data.bill_forecast else None
        ),
    ),
    # New bill forecast sensors - Diagnostic
    DominionEnergySensorDescription(
        key="billing_period_start",
        translation_key="billing_period_start",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.bill_forecast.current_period_start if data.bill_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="billing_period_end",
        translation_key="billing_period_end",
        device_class=SensorDeviceClass.DATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            data.bill_forecast.current_period_end if data.bill_forecast else None
        ),
    ),
    DominionEnergySensorDescription(
        key="is_time_of_use",
        translation_key="is_time_of_use",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: (
            "Yes" if data.bill_forecast and data.bill_forecast.is_tou else "No"
        ),
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

    device_info = DeviceInfo(
        identifiers={(DOMAIN, account_number)},
        name=f"Dominion Energy {account_number}",
        manufacturer="Dominion Energy",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://mya.dominionenergy.com",
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
        for description in SENSORS
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
