from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_LEGACY_PROFILE, CONF_PERSON, DOMAIN
from .coordinator import HuaweiScaleCoordinator

_OHM = "Ω"
SYNC_STATUS_OPTIONS = ("disabled", "pending", "synced", "error")

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

SYNC_STATUS_DESCRIPTION = SensorEntityDescription(
    key="sparky_sync",
    translation_key="sparky_sync",
    device_class=SensorDeviceClass.ENUM,
    options=list(SYNC_STATUS_OPTIONS),
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[HuaweiScaleCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    for profile_id, profile in coordinator.profiles.items():
        entities = [
            HuaweiAH100Sensor(coordinator, profile_id, profile, description)
            for description in SENSOR_DESCRIPTIONS
        ]
        entities.append(HuaweiAH100SyncStatusSensor(coordinator, profile_id, profile))
        async_add_entities(entities, config_subentry_id=profile_id)


def _profile_device_info(
    coordinator: HuaweiScaleCoordinator, profile_id: str, profile: Mapping[str, Any]
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.address}_{profile_id}")},
        name=profile["name"],
        manufacturer="Huawei",
        model="AH100 profile",
        entry_type=DeviceEntryType.SERVICE,
        via_device=(DOMAIN, coordinator.address),
    )


class HuaweiAH100Sensor(CoordinatorEntity[HuaweiScaleCoordinator], SensorEntity):
    """Expose the latest measurement stored for one profile."""

    entity_description: SensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HuaweiScaleCoordinator,
        profile_id: str,
        profile: Mapping[str, Any],
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self.profile_id = profile_id
        self.profile = profile
        self._attr_unique_id = (
            f"{coordinator.address}_{description.key}"
            if profile.get(CONF_LEGACY_PROFILE) is True
            else f"{coordinator.address}_{profile_id}_{description.key}"
        )

    @property
    def available(self) -> bool:
        return self.coordinator.profile_data(self.profile_id) is not None

    @property
    def native_value(self) -> float | int | datetime | None:
        data = self.coordinator.profile_data(self.profile_id)
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
        return _profile_device_info(self.coordinator, self.profile_id, self.profile)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        data = self.coordinator.profile_data(self.profile_id)
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
        attributes["profile_id"] = self.profile_id
        if person_entity_id := self.profile.get(CONF_PERSON):
            attributes["person_entity_id"] = person_entity_id
        return attributes


class HuaweiAH100SyncStatusSensor(CoordinatorEntity[HuaweiScaleCoordinator], SensorEntity):
    """Expose the sync state for one profile's SparkyFitness destination."""

    entity_description = SYNC_STATUS_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HuaweiScaleCoordinator,
        profile_id: str,
        profile: Mapping[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self.profile_id = profile_id
        self.profile = profile
        self._attr_unique_id = f"{coordinator.address}_{profile_id}_sparky_sync"

    @property
    def available(self) -> bool:
        return self.profile_id in self.coordinator.profiles

    @property
    def native_value(self) -> str:
        return self.coordinator.sync_status(self.profile_id)

    @property
    def device_info(self) -> DeviceInfo:
        return _profile_device_info(self.coordinator, self.profile_id, self.profile)
