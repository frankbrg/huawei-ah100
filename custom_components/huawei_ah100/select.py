"""Expose the physical scale's shared active-profile selector."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import HuaweiScaleCoordinator

SELECT_DESCRIPTION = SelectEntityDescription(
    key="active_profile",
    translation_key="active_profile",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[HuaweiScaleCoordinator],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the active-profile selector for one scale entry."""
    async_add_entities([HuaweiAH100ActiveProfileSelect(entry.runtime_data)])


class HuaweiAH100ActiveProfileSelect(CoordinatorEntity[HuaweiScaleCoordinator], SelectEntity):
    """Select which profile the physical scale uses for its next session."""

    entity_description = SELECT_DESCRIPTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: HuaweiScaleCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_active_profile"

    @property
    def available(self) -> bool:
        return bool(self.coordinator.profiles)

    @property
    def options(self) -> list[str]:
        return [profile["name"] for profile in self.coordinator.profiles.values()]

    @property
    def current_option(self) -> str | None:
        if self.coordinator.active_profile_id is None:
            return None
        profile = self.coordinator.profiles.get(self.coordinator.active_profile_id)
        return profile["name"] if profile is not None else None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.address)},
            name=NAME,
            manufacturer="Huawei",
            model="AH100",
        )

    async def async_select_option(self, option: str) -> None:
        """Select the named profile for the next manual weighing session."""
        profile_id = next(
            (
                profile_id
                for profile_id, profile in self.coordinator.profiles.items()
                if profile["name"] == option
            ),
            None,
        )
        if profile_id is None:
            raise ValueError("Unknown profile option")
        await self.coordinator.async_select_profile(profile_id)
