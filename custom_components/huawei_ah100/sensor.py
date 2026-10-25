from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import HuaweiScaleCoordinator

_OHM = "Ω"

SENSOR_DESCRIPTIONS = (
    SensorEntityDescription(
        key="weight_kg",
        translation_key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="fat_percent",
        translation_key="body_fat",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="impedance_ohm",
        translation_key="impedance",
        native_unit_of_measurement=_OHM,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="received_at",
        translation_key="last_measurement",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[HuaweiScaleCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for a configured scale."""
    coordinator: HuaweiScaleCoordinator = entry.runtime_data
    async_add_entities(
        HuaweiAH100Sensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class HuaweiAH100Sensor(CoordinatorEntity[HuaweiScaleCoordinator], SensorEntity):
    """Represent one value reported by a Huawei AH100 scale."""

    entity_description: SensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HuaweiScaleCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None

    @property
    def native_value(self) -> float | int | datetime | None:
        data = self.coordinator.data
        if data is None:
            return None
        value = data.get(self.entity_description.key)
        if value is None:
            return None
        if self.entity_description.key == "received_at":
            return datetime.fromisoformat(value)
        return value

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=NAME,
            manufacturer="Huawei",
            model="AH100",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        attributes = {
            attribute: data[key]
            for attribute, key in (
                ("user_id", "user_id"),
                ("device_measured_at", "measured_at"),
                ("weekday", "weekday"),
                ("unknown_tail_hex", "unknown_tail_hex"),
            )
            if key in data and data[key] is not None
        }
        return attributes or None
