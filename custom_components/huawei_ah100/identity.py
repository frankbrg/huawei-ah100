"""Track short-lived profile claims tied to individual scale wake events."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .const import CONF_PERSON, DOMAIN

CLAIM_TIMEOUT_SECONDS = 45.0


@dataclass
class WeighingRequest:
    """State needed to resolve one wake-specific profile request."""

    request_id: str
    wake_id: int
    deadline: float
    future: asyncio.Future[str | None]
    timer: asyncio.TimerHandle
    profile_id: str | None = None


class IdentityRequests:
    """Emit profile requests and apply the first valid claim for each wake."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        profiles: Mapping[str, Mapping[str, Any]],
        on_claim: Callable[[str], None],
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._profiles = profiles
        self._on_claim = on_claim
        self._request: WeighingRequest | None = None

    @callback
    def request(self, wake_id: int) -> asyncio.Future[str | None]:
        """Emit a request and return the future resolved by a claim or cancellation."""
        self.cancel("superseded")
        request_id = uuid4().hex
        future = self._hass.loop.create_future()
        timer = self._hass.loop.call_later(CLAIM_TIMEOUT_SECONDS, self.cancel, "expired", wake_id)
        self._request = WeighingRequest(
            request_id,
            wake_id,
            self._hass.loop.time() + CLAIM_TIMEOUT_SECONDS,
            future,
            timer,
        )
        self._hass.bus.async_fire(
            f"{DOMAIN}_weighing_requested",
            {
                "entry_id": self._entry_id,
                "request_id": request_id,
                "expires_at": (
                    dt_util.utcnow() + timedelta(seconds=CLAIM_TIMEOUT_SECONDS)
                ).isoformat(),
                "profiles": [self._profile_payload(profile_id) for profile_id in self._profiles],
            },
        )
        return future

    def _profile_payload(self, profile_id: str) -> dict[str, Any]:
        profile = self._profiles[profile_id]
        payload = {"profile_id": profile_id, "name": profile["name"]}
        if person := profile.get(CONF_PERSON):
            payload[CONF_PERSON] = person
        return payload

    def claim(
        self,
        request_id: str,
        profile_id: str,
        *,
        claimant_user_id: str | None = None,
        context_user_id: str | None = None,
    ) -> None:
        """Accept a profile once while the matching wake request is live."""
        request = self._request
        if (
            request is not None
            and request.profile_id is None
            and self._hass.loop.time() >= request.deadline
        ):
            self.cancel("expired", request.wake_id)
            request = None
        if request is None or request.request_id != request_id or request.profile_id is not None:
            raise ServiceValidationError("The weighing request is no longer available")
        if profile_id not in self._profiles:
            raise ServiceValidationError("The selected profile is unavailable")
        # Automations should forward the original HA context, not notification action data.
        if claimant_user_id is not None or context_user_id is not None:
            person = self._profiles[profile_id].get(CONF_PERSON)
            state = self._hass.states.get(person) if isinstance(person, str) else None
            user_id = state.attributes.get("user_id") if state is not None else None
            if (
                not isinstance(user_id, str)
                or not user_id
                or (claimant_user_id is not None and claimant_user_id != user_id)
                or (context_user_id is not None and context_user_id != user_id)
            ):
                raise ServiceValidationError("The selected profile is unavailable to this user")
        request.profile_id = profile_id
        request.timer.cancel()
        self._on_claim(profile_id)
        request.future.set_result(profile_id)
        payload = self._profile_payload(profile_id)
        payload.pop("name")
        self._hass.bus.async_fire(
            f"{DOMAIN}_profile_confirmed",
            {"entry_id": self._entry_id, "request_id": request_id, **payload},
        )

    def cancel(self, reason: str, wake_id: int | None = None) -> None:
        """Invalidate a pending request and emit its cancellation reason."""
        request = self._request
        if request is None or (wake_id is not None and request.wake_id != wake_id):
            return
        self._request = None
        request.timer.cancel()
        if not request.future.done():
            request.future.set_result(None)
        payload = {
            "entry_id": self._entry_id,
            "request_id": request.request_id,
            "reason": reason,
        }
        if request.profile_id is not None:
            profile = self._profile_payload(request.profile_id)
            profile.pop("name")
            payload.update(profile)
        self._hass.bus.async_fire(f"{DOMAIN}_weighing_cancelled", payload)

    def finish(self, wake_id: int) -> None:
        """Forget a request after its measurement has been saved."""
        request = self._request
        if request is not None and request.wake_id == wake_id:
            self._request = None
            request.timer.cancel()
