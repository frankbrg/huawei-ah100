"""Register service handlers for profile confirmation."""

from __future__ import annotations

import probatio
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

SERVICE_CLAIM_WEIGHING = "claim_weighing"
CLAIM_WEIGHING_SCHEMA = probatio.Schema(
    {
        probatio.Required("entry_id"): cv.string,
        probatio.Required("request_id"): cv.string,
        probatio.Required("profile_id"): cv.string,
        probatio.Optional("claimant_user_id"): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's weighing-claim service once per Home Assistant instance."""
    if hass.services.has_service(DOMAIN, SERVICE_CLAIM_WEIGHING):
        return

    async def claim_weighing(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["entry_id"])
        coordinator = getattr(entry, "runtime_data", None)
        if entry is None or entry.domain != DOMAIN or coordinator is None:
            raise ServiceValidationError("The scale is unavailable")
        await coordinator.async_claim_weighing(
            call.data["request_id"],
            call.data["profile_id"],
            claimant_user_id=call.data.get("claimant_user_id"),
            context_user_id=call.context.user_id,
        )

    hass.services.async_register(
        DOMAIN, SERVICE_CLAIM_WEIGHING, claim_weighing, schema=CLAIM_WEIGHING_SCHEMA
    )
