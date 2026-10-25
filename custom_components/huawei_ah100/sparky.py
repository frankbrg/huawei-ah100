from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_SPARKY_API_KEY, CONF_SPARKY_ENABLED, CONF_SPARKY_URL

REQUEST_TIMEOUT_SECONDS = 15


class SparkySyncError(Exception):
    pass


def normalize_base_url(value: str) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError("invalid SparkyFitness URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid SparkyFitness URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or "?" in value
        or "#" in value
        or "\\" in value
        or (port is not None and not 0 < port <= 65535)
    ):
        raise ValueError("invalid SparkyFitness URL")
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


@dataclass(frozen=True)
class SparkyDestination:
    """Credentials for one profile's SparkyFitness account."""

    base_url: str
    api_key: str = field(repr=False)

    @property
    def fingerprint(self) -> str:
        """Identify this destination without storing its credentials in sync state."""
        return hashlib.sha256(f"{self.base_url}\0{self.api_key}".encode()).hexdigest()


def destination_from_profile(profile: Mapping[str, Any]) -> SparkyDestination | None:
    if not profile.get(CONF_SPARKY_ENABLED, False):
        return None
    key = profile.get(CONF_SPARKY_API_KEY)
    if not isinstance(key, str) or not key.strip() or "\r" in key or "\n" in key:
        raise ValueError("invalid SparkyFitness API key")
    return SparkyDestination(normalize_base_url(profile.get(CONF_SPARKY_URL)), key)


def measurement_day(measurement: Mapping[str, Any], time_zone: str) -> str:
    received_at = datetime.fromisoformat(measurement["received_at"])
    if received_at.utcoffset() is None:
        raise ValueError("measurement timestamp must have a time zone")
    return received_at.astimezone(ZoneInfo(time_zone)).date().isoformat()


def measurement_payload(
    measurement: Mapping[str, Any], time_zone: str, *, day: str | None = None
) -> list[dict[str, Any]]:
    if day is None:
        day = measurement_day(measurement, time_zone)
    elif not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
        raise ValueError("invalid measurement day")
    weight = measurement["weight_kg"]
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not math.isfinite(weight)
        or not 0 < weight <= 300
    ):
        raise ValueError("invalid measurement weight")
    payload = [{"type": "weight", "value": weight, "date": day}]
    fat = measurement.get("fat_percent")
    if (
        not isinstance(fat, bool)
        and isinstance(fat, (int, float))
        and math.isfinite(fat)
        and 0 < fat <= 100
    ):
        payload.append({"type": "body_fat", "value": fat, "date": day})
    return payload


class SparkyClient:
    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def async_send(
        self,
        destination: SparkyDestination,
        measurement: Mapping[str, Any],
        time_zone: str,
        *,
        day: str | None = None,
    ) -> None:
        payload = measurement_payload(measurement, time_zone, day=day)
        client = async_get_clientsession(self._hass)
        async with client.post(
            f"{destination.base_url}/api/measurements/health-data",
            headers={"Authorization": f"Bearer {destination.api_key}"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            allow_redirects=False,
        ) as response:
            if not 200 <= response.status < 300:
                raise SparkySyncError("SparkyFitness rejected the request")
            try:
                result = await response.json()
            except ValueError, aiohttp.ContentTypeError:
                raise SparkySyncError("invalid SparkyFitness response") from None
        if (
            not isinstance(result, dict)
            or result.get("errors") != []
            or result.get("skipped") != []
        ):
            raise SparkySyncError("SparkyFitness did not save all measurements")
        processed = result.get("processed")
        if (
            not isinstance(processed, list)
            or len(processed) != len(payload)
            or any(
                not isinstance(item, dict)
                or item.get("status") != "success"
                or not isinstance(item.get("type"), str)
                for item in processed
            )
            or sorted(item.get("type", "") for item in processed)
            != sorted(item["type"] for item in payload)
        ):
            raise SparkySyncError("SparkyFitness did not confirm all measurements")
