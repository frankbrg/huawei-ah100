"""Set up and unload the Huawei AH100 Home Assistant integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import HuaweiScaleCoordinator

PLATFORMS = [Platform.SENSOR]
type HuaweiScaleConfigEntry = ConfigEntry[HuaweiScaleCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: HuaweiScaleConfigEntry) -> bool:
    """Initialize the coordinator and forward the sensor platform setup."""
    coordinator = HuaweiScaleCoordinator(hass, entry)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_stop()
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration after profile options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HuaweiScaleConfigEntry) -> bool:
    """Unload the sensor platform and stop the coordinator when successful."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.async_stop()
    return unloaded
