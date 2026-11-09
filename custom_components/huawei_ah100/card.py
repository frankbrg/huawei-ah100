"""Serve the bundled dashboard cards so they work without a manual resource entry."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

CARD_URL = f"/{DOMAIN}/huawei-ah100-card.js"
CARD_PATH = Path(__file__).parent / "www" / "huawei-ah100-card.js"
_DATA_CARD_REGISTERED = f"{DOMAIN}_card_registered"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card script once and load it in every dashboard."""
    if hass.data.get(_DATA_CARD_REGISTERED):
        return
    hass.data[_DATA_CARD_REGISTERED] = True
    integration = await async_get_integration(hass, DOMAIN)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    # The version busts the browser cache after an update of the integration.
    add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
