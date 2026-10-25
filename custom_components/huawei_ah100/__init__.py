"""Set up and unload the Huawei AH100 Home Assistant integration."""

from __future__ import annotations

from types import MappingProxyType

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ACTIVE_PROFILE,
    CONF_ALLOW_BIND,
    CONF_LEGACY_PROFILE,
    CONF_USER_ID,
    DOMAIN,
    NAME,
    PROFILE_KEYS,
    SUBENTRY_PROFILE,
)
from .coordinator import HuaweiScaleCoordinator

PLATFORMS = [Platform.SELECT, Platform.SENSOR]
type HuaweiScaleConfigEntry = ConfigEntry[HuaweiScaleCoordinator]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Convert the single-profile entry while preserving its existing sensor IDs."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        settings = {**entry.data, **entry.options}
        primary = next(
            (
                profile
                for profile in entry.subentries.values()
                if profile.subentry_type == SUBENTRY_PROFILE
                and profile.data.get(CONF_LEGACY_PROFILE)
            ),
            None,
        )
        if primary is None:
            primary = ConfigSubentry(
                data=MappingProxyType(
                    {**{key: settings[key] for key in PROFILE_KEYS}, CONF_LEGACY_PROFILE: True}
                ),
                subentry_type=SUBENTRY_PROFILE,
                title="Profile 1",
                unique_id="primary",
            )
            hass.config_entries.async_add_subentry(entry, primary)
        options = {key: value for key, value in entry.options.items() if key not in PROFILE_KEYS}
        options[CONF_ACTIVE_PROFILE] = primary.subentry_id
        hass.config_entries.async_update_entry(
            entry,
            data={
                CONF_ADDRESS: settings[CONF_ADDRESS],
                CONF_USER_ID: settings.get(CONF_USER_ID, 1),
                CONF_ALLOW_BIND: settings.get(CONF_ALLOW_BIND, False),
            },
            options=options,
            version=2,
            minor_version=1,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HuaweiScaleConfigEntry) -> bool:
    """Initialize the coordinator and forward the sensor platform setup."""
    coordinator = HuaweiScaleCoordinator(hass, entry)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_start()
        dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, coordinator.address)},
            name=NAME,
            manufacturer="Huawei",
            model="AH100",
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_stop()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after profile options change."""
    await entry.runtime_data.async_prepare_reload()
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HuaweiScaleConfigEntry) -> bool:
    """Unload the sensor platform and stop the coordinator when successful."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unloaded
