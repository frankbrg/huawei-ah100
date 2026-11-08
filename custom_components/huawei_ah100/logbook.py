"""Describe profile assignment events for the logbook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, NAME

EVENT_PROFILE_ACTIVATED = f"{DOMAIN}_profile_activated"
EVENT_PROFILE_DEACTIVATED = f"{DOMAIN}_profile_deactivated"
EVENT_MEASUREMENT_SAVED = f"{DOMAIN}_measurement_saved"
EVENT_MEASUREMENT_REJECTED = f"{DOMAIN}_measurement_rejected"

METHODS = {
    "selection": "direct selection",
    "confirmation": "confirmation",
    "proximity": "phone proximity",
    "weight": "weight",
    "single_profile": "the only profile",
}
DEACTIVATION_REASONS = {
    "weighing_saved": "weighing saved",
    "replaced": "replaced by another profile",
    "out_of_range": "phone out of range",
    "deselected": "selection cleared",
}
REJECTION_REASONS = {
    "weight_mismatch": "weight does not fit the profile",
    "ambiguous_weight": "weight fits several profiles",
    "no_matching_profile": "weight fits no profile",
}


def _method(data: dict[str, Any]) -> str:
    return METHODS.get(data.get("method") or data.get("assigned_by"), "unknown method")


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, str]]], None],
) -> None:
    """Describe the integration's events in the logbook."""

    @callback
    def describe_activated(event: Event) -> dict[str, str]:
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_MESSAGE: (
                f"activated profile {event.data.get('profile_name')} by {_method(event.data)}"
            ),
        }

    @callback
    def describe_deactivated(event: Event) -> dict[str, str]:
        reason = event.data.get("reason")
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_MESSAGE: (
                f"deactivated profile {event.data.get('profile_name')} "
                f"(activated by {_method(event.data)}): "
                f"{DEACTIVATION_REASONS.get(reason, reason)}"
            ),
        }

    @callback
    def describe_saved(event: Event) -> dict[str, str]:
        message = f"saved {event.data.get('weight_kg')} kg, assigned by {_method(event.data)}"
        if redirected_from := event.data.get("redirected_from"):
            message += f", moved from profile {redirected_from}"
        return {LOGBOOK_ENTRY_NAME: NAME, LOGBOOK_ENTRY_MESSAGE: message}

    @callback
    def describe_rejected(event: Event) -> dict[str, str]:
        reason = event.data.get("reason")
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_MESSAGE: (
                f"discarded {event.data.get('weight_kg')} kg: "
                f"{REJECTION_REASONS.get(reason, reason)}"
            ),
        }

    async_describe_event(DOMAIN, EVENT_PROFILE_ACTIVATED, describe_activated)
    async_describe_event(DOMAIN, EVENT_PROFILE_DEACTIVATED, describe_deactivated)
    async_describe_event(DOMAIN, EVENT_MEASUREMENT_SAVED, describe_saved)
    async_describe_event(DOMAIN, EVENT_MEASUREMENT_REJECTED, describe_rejected)
