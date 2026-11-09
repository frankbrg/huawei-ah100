"""Describe profile assignment events for the logbook."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ICON,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN, NAME

EVENT_PROFILE_ACTIVATED = f"{DOMAIN}_profile_activated"
EVENT_PROFILE_DEACTIVATED = f"{DOMAIN}_profile_deactivated"
EVENT_MEASUREMENT_SAVED = f"{DOMAIN}_measurement_saved"
EVENT_MEASUREMENT_REJECTED = f"{DOMAIN}_measurement_rejected"
EVENT_MULTIPLE_PHONES_NEARBY = f"{DOMAIN}_multiple_phones_nearby"

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
    "multiple_phones_nearby": "phones of several profiles nearby",
    "deselected": "selection cleared",
}
REJECTION_REASONS = {
    "weight_mismatch": "weight does not fit the profile",
    "ambiguous_weight": "weight fits several profiles",
    "no_matching_profile": "weight fits no profile",
}


def _profile_name(hass: HomeAssistant, entry_id: Any, profile_id: Any) -> str:
    entry = hass.config_entries.async_get_entry(entry_id) if isinstance(entry_id, str) else None
    if entry is not None and (subentry := entry.subentries.get(profile_id)) is not None:
        return subentry.title
    return str(profile_id)


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
            LOGBOOK_ENTRY_ICON: "mdi:account-check",
            LOGBOOK_ENTRY_MESSAGE: (
                f"activated profile {event.data.get('profile_name')} by {_method(event.data)}"
            ),
        }

    @callback
    def describe_deactivated(event: Event) -> dict[str, str]:
        reason = event.data.get("reason")
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_ICON: "mdi:account-off-outline",
            LOGBOOK_ENTRY_MESSAGE: (
                f"deactivated profile {event.data.get('profile_name')} "
                f"(activated by {_method(event.data)}): "
                f"{DEACTIVATION_REASONS.get(reason, reason)}"
            ),
        }

    @callback
    def describe_saved(event: Event) -> dict[str, str]:
        entry_id = event.data.get("entry_id")
        name = event.data.get("profile_name") or _profile_name(
            hass, entry_id, event.data.get("profile_id")
        )
        message = (
            f"saved {event.data.get('weight_kg')} kg for {name}, assigned by {_method(event.data)}"
        )
        if redirected_from := event.data.get("redirected_from"):
            message += f", moved from profile {_profile_name(hass, entry_id, redirected_from)}"
        if event.data.get("weight_check_overridden"):
            message += ", kept despite the weight check because it was selected directly"
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_ICON: "mdi:scale-bathroom",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    @callback
    def describe_rejected(event: Event) -> dict[str, str]:
        reason = event.data.get("reason")
        entry_id = event.data.get("entry_id")

        def names(profile_ids: list[str]) -> str:
            return ", ".join(
                _profile_name(hass, entry_id, profile_id) for profile_id in profile_ids
            )

        message = f"discarded {event.data.get('weight_kg')} kg"
        if profile_id := event.data.get("profile_id"):
            message += f" for {_profile_name(hass, entry_id, profile_id)}"
        message += f": {REJECTION_REASONS.get(reason, reason)}"
        if candidates := event.data.get("candidates"):
            message += f" ({names(candidates)})"
        if nearby := event.data.get("nearby_profiles"):
            message += f", phones of several profiles were nearby ({names(nearby)})"
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_ICON: "mdi:scale-unbalanced",
            LOGBOOK_ENTRY_MESSAGE: message,
        }

    @callback
    def describe_multiple_phones(event: Event) -> dict[str, str]:
        names = ", ".join(event.data.get("profile_names", []))
        return {
            LOGBOOK_ENTRY_NAME: NAME,
            LOGBOOK_ENTRY_ICON: "mdi:cellphone-wireless",
            LOGBOOK_ENTRY_MESSAGE: f"phones of several profiles nearby ({names}), "
            "phone proximity cannot choose a profile",
        }

    async_describe_event(DOMAIN, EVENT_PROFILE_ACTIVATED, describe_activated)
    async_describe_event(DOMAIN, EVENT_PROFILE_DEACTIVATED, describe_deactivated)
    async_describe_event(DOMAIN, EVENT_MEASUREMENT_SAVED, describe_saved)
    async_describe_event(DOMAIN, EVENT_MEASUREMENT_REJECTED, describe_rejected)
    async_describe_event(DOMAIN, EVENT_MULTIPLE_PHONES_NEARBY, describe_multiple_phones)
