"""Coordinate scale sessions, profile readings, and optional SparkyFitness sync."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Coroutine, Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOW_BIND,
    CONF_BEACON_UUIDS,
    CONF_INITIAL_WEIGHT,
    CONF_LEGACY_PROFILE,
    CONF_PERSON,
    CONF_PROXIMITY_ENABLED,
    CONF_PROXIMITY_RSSI,
    CONF_REQUIRE_PROFILE_CONFIRMATION,
    CONF_SPARKY_ENABLED,
    CONF_USER_ID,
    CONF_WEIGHT_CHECK,
    DEFAULT_PROXIMITY_RSSI,
    DOMAIN,
    METHOD_CONFIRMATION,
    METHOD_PROXIMITY,
    METHOD_SELECTION,
    METHOD_SINGLE_PROFILE,
    METHOD_WEIGHT,
    NAME,
    PROFILE_KEYS,
    SUBENTRY_PROFILE,
)
from .identity import IdentityRequests
from .plausibility import (
    REFERENCE_MAX_AGE,
    Outcome,
    WeightReference,
    check_weight,
    matching_profiles,
)
from .proximity import PhoneProximity
from .session import AuthenticationError, SessionProfile, run_session
from .sparky import SparkyClient, destination_from_profile, measurement_day
from .storage import MeasurementStore

_LOGGER = logging.getLogger(__name__)
SYNC_RETRY_SECONDS = 5
SYNC_MAX_RETRY_SECONDS = 300
PROXIMITY_POLL_INTERVAL = timedelta(seconds=5)


class HuaweiScaleCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinate one physical scale with readings and sync state scoped per profile."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=NAME, config_entry=entry)
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self._settings = {**entry.data, **entry.options}
        self._profiles = MappingProxyType(
            {
                subentry_id: MappingProxyType({"name": subentry.title, **subentry.data})
                for subentry_id, subentry in entry.subentries.items()
                if subentry.subentry_type == SUBENTRY_PROFILE
            }
        )
        self._legacy_profile_id = next(
            (key for key, profile in self._profiles.items() if profile.get(CONF_LEGACY_PROFILE)),
            None,
        )
        self._default_profile_id = self._legacy_profile_id or next(iter(self._profiles), None)
        # A direct selection only applies to the next weighing and is not persisted.
        self._selected_profile_id: str | None = None
        self._preset: tuple[str, str] | None = None
        self._claim: tuple[str, str] | None = None
        self._beacon_profile_id: str | None = None
        # Profiles whose phones are all in range at once, so proximity cannot decide.
        self._crowded_profile_ids: list[str] = []
        self._last_rejection: dict[str, Any] | None = None
        self._assignment: tuple[str, str] | None = None
        self._assignment_since: datetime | None = None
        self._session_assignment: tuple[str, str] | None = None
        self._unsubscribe_proximity = None
        self._destinations = {}
        self._destination_errors: set[str] = set()
        for profile_id, profile in self._profiles.items():
            try:
                self._destinations[profile_id] = destination_from_profile(profile)
            except ValueError:
                self._destinations[profile_id] = None
                self._destination_errors.add(profile_id)
        self._store = MeasurementStore(
            hass, hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}.json")
        )
        self._storage_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._sync_wake = asyncio.Event()
        self._session_done = asyncio.Event()
        self._session_done.set()
        self._session_stop: asyncio.Event | None = None
        self._paused = False
        self._task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._unsubscribe = None
        self._readings: dict[str, dict[str, Any]] = {}
        self._pending_sync: dict[str, dict[str, Any]] = {}
        self._sync_state: dict[str, dict[str, Any]] = {}
        self._sparky = SparkyClient(hass)
        self._identity = IdentityRequests(
            hass, entry.entry_id, self._profiles, self._profile_claimed, self._claim_released
        )
        beacons = {
            beacon_uuid: profile_id
            for profile_id, profile in self._profiles.items()
            for beacon_uuid in profile.get(CONF_BEACON_UUIDS, ())
        }
        self._proximity = PhoneProximity(hass, self.address, beacons) if beacons else None
        self.data = {}

    @property
    def profiles(self) -> Mapping[str, Mapping[str, Any]]:
        """Return the configured profiles keyed by subentry ID."""
        return self._profiles

    @property
    def active_profile_id(self) -> str | None:
        """Return the profile directly selected for the next weighing, if any."""
        return self._selected_profile_id

    @property
    def assignment(self) -> tuple[str, str] | None:
        """Return the currently activated profile and the method that activated it."""
        return self._assignment

    @property
    def assignment_since(self) -> datetime | None:
        """Return when the current profile was activated."""
        return self._assignment_since

    @property
    def last_rejection(self) -> dict[str, Any] | None:
        """Return the most recent discarded weighing since Home Assistant started."""
        return self._last_rejection

    def profile_data(self, profile_id: str) -> dict[str, Any] | None:
        """Return the latest stored measurement for a profile, if available."""
        reading = self._readings.get(profile_id)
        return dict(reading) if reading is not None else None

    def phone_rssi(self, profile_id: str) -> int | None:
        """Return the strongest recent RSSI of the profile's phones at the scale's scanner."""
        if self._proximity is None:
            return None
        return self._proximity.rssi_by_profile().get(profile_id)

    def _nearby_profile(self) -> str | None:
        if self._proximity is None or not self._settings.get(CONF_PROXIMITY_ENABLED, False):
            return None
        return self._proximity.nearby_profile(
            self._settings.get(CONF_PROXIMITY_RSSI, DEFAULT_PROXIMITY_RSSI)
        )

    def _profiles_in_range(self) -> list[str]:
        if self._proximity is None or not self._settings.get(CONF_PROXIMITY_ENABLED, False):
            return []
        return self._proximity.profiles_in_range(
            self._settings.get(CONF_PROXIMITY_RSSI, DEFAULT_PROXIMITY_RSSI)
        )

    @callback
    def _async_update_proximity(self, _now: datetime | None = None) -> None:
        beacon_profile_id = self._nearby_profile()
        crowded = [] if beacon_profile_id is not None else self._profiles_in_range()
        if len(crowded) < 2:
            crowded = []
        if crowded != self._crowded_profile_ids:
            self._crowded_profile_ids = crowded
            if crowded:
                self._multiple_phones_event(crowded)
        if beacon_profile_id != self._beacon_profile_id:
            self._beacon_profile_id = beacon_profile_id
            if beacon_profile_id is not None:
                reason = "replaced"
            elif crowded:
                reason = "multiple_phones_nearby"
            else:
                reason = "out_of_range"
            self._refresh_assignment(reason)

    def _multiple_phones_event(self, profile_ids: list[str]) -> None:
        names = [self._profiles[profile_id]["name"] for profile_id in profile_ids]
        _LOGGER.info("Phones of several profiles are near the scale: %s", ", ".join(names))
        self.hass.bus.async_fire(
            f"{DOMAIN}_multiple_phones_nearby",
            {
                "entry_id": self.entry.entry_id,
                "profile_ids": profile_ids,
                "profile_names": names,
            },
        )

    def _current_assignment(self) -> tuple[str, str] | None:
        if self._claim is not None:
            return self._claim
        if self._selected_profile_id is not None:
            return self._selected_profile_id, METHOD_SELECTION
        if self._beacon_profile_id is not None:
            return self._beacon_profile_id, METHOD_PROXIMITY
        return None

    @callback
    def _refresh_assignment(self, reason: str) -> None:
        """Log profile activations and deactivations as they happen."""
        new = self._current_assignment()
        old = self._assignment
        if new == old:
            return
        if old is not None:
            self._assignment_event("profile_deactivated", old, reason=reason)
        self._assignment = new
        self._assignment_since = dt_util.utcnow() if new is not None else None
        if new is not None:
            self._assignment_event("profile_activated", new)
        self.async_update_listeners()

    def _assignment_event(
        self, event: str, assignment: tuple[str, str], *, reason: str | None = None
    ) -> None:
        profile_id, method = assignment
        payload = {
            "entry_id": self.entry.entry_id,
            "profile_id": profile_id,
            "profile_name": self._profiles[profile_id]["name"],
            "method": method,
        }
        if person := self._profiles[profile_id].get(CONF_PERSON):
            payload[CONF_PERSON] = person
        if reason is not None:
            payload["reason"] = reason
        _LOGGER.info(
            "Profile %s %s by %s%s",
            payload["profile_name"],
            "activated" if reason is None else "deactivated",
            method,
            "" if reason is None else f" ({reason})",
        )
        self.hass.bus.async_fire(f"{DOMAIN}_{event}", payload)

    def sync_status(self, profile_id: str) -> str:
        """Return the profile's current SparkyFitness sync state."""
        if profile_id in self._destination_errors:
            return "error"
        if self._destinations.get(profile_id) is None:
            return "disabled"
        return self._sync_state.get(profile_id, {}).get("status", "pending")

    async def async_start(self) -> None:
        """Restore saved data and start Bluetooth discovery and sync workers."""
        stored = await self._store.async_load()
        readings: dict[str, dict[str, Any]] = {}
        pending: dict[str, dict[str, Any]] = {}
        states: dict[str, dict[str, Any]] = {}
        if isinstance(stored, dict) and stored.get("version") == 2:
            if isinstance(stored.get("profiles"), dict):
                readings = {
                    profile_id: reading
                    for profile_id, reading in stored["profiles"].items()
                    if profile_id in self._profiles
                    and self._valid_stored_measurement(reading, match_user_id=False)
                }
            if isinstance(stored.get("pending_sync"), dict):
                pending = {
                    key: record
                    for key, record in stored["pending_sync"].items()
                    if self._valid_pending_record(key, record)
                }
            if isinstance(stored.get("sync_state"), dict):
                states = {
                    profile_id: state
                    for profile_id, state in stored["sync_state"].items()
                    if self._valid_sync_state(profile_id, state)
                }
            for record in pending.values():
                profile_id = record["profile_id"]
                if states.get(profile_id, {}).get("status") != "error":
                    states[profile_id] = {
                        "destination": record["destination"],
                        "status": "pending",
                    }
        elif self._legacy_profile_id and self._valid_stored_measurement(stored):
            readings[self._legacy_profile_id] = stored
        async with self._storage_lock:
            if stored is not None and stored != self._envelope(readings, pending, states):
                await self._store.async_save(self._envelope(readings, pending, states))
            self._readings, self._pending_sync, self._sync_state = readings, pending, states
        self._publish()
        self._sync_task = self.hass.async_create_background_task(
            self._async_sync(), f"{DOMAIN} SparkyFitness sync", eager_start=False
        )
        if pending:
            self._sync_wake.set()
        if not self._profiles:
            return
        if self._proximity is not None:
            self._proximity.async_start()
            if self._settings.get(CONF_PROXIMITY_ENABLED, False):
                self._unsubscribe_proximity = async_track_time_interval(
                    self.hass, self._async_update_proximity, PROXIMITY_POLL_INTERVAL
                )
        self._unsubscribe = bluetooth.async_register_advertisement_callback(
            self.hass,
            self._advertisement,
            self.address,
        )
        if self._get_device() is not None:
            self._wake.set()
        self._task = self.hass.async_create_background_task(
            self._async_listen(), f"{DOMAIN} BLE listener", eager_start=False
        )

    def _valid_stored_measurement(self, stored: Any, *, match_user_id: bool = True) -> bool:
        if not isinstance(stored, dict):
            return False
        weight = stored.get("weight_kg")
        try:
            received_at = datetime.fromisoformat(stored["received_at"])
        except KeyError, TypeError, ValueError:
            return False
        return (
            not isinstance(weight, bool)
            and isinstance(weight, (int, float))
            and math.isfinite(weight)
            and 0 < weight <= 300
            and isinstance(stored.get("user_id"), int)
            and not isinstance(stored["user_id"], bool)
            and 0 <= stored["user_id"] <= 255
            and (not match_user_id or stored["user_id"] == self._settings.get(CONF_USER_ID, 1))
            and received_at.utcoffset() is not None
        )

    def _valid_pending_record(self, key: Any, record: Any) -> bool:
        if not isinstance(record, dict) or not isinstance(key, str):
            return False
        profile_id = record.get("profile_id")
        if not isinstance(profile_id, str):
            return False
        destination = self._destinations.get(profile_id)
        day = record.get("day")
        try:
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                return False
        except ValueError:
            return False
        attempts = record.get("attempts", 0)
        retry_at = record.get("retry_at", 0)
        return (
            destination is not None
            and record.get("destination") == destination.fingerprint
            and isinstance(day, str)
            and key == f"{profile_id}:{day}"
            and self._valid_stored_measurement(record.get("measurement"), match_user_id=False)
            and isinstance(attempts, int)
            and not isinstance(attempts, bool)
            and attempts >= 0
            and isinstance(retry_at, (int, float))
            and not isinstance(retry_at, bool)
            and math.isfinite(retry_at)
        )

    def _valid_sync_state(self, profile_id: str, state: Any) -> bool:
        destination = self._destinations.get(profile_id)
        return (
            destination is not None
            and isinstance(state, dict)
            and state.get("destination") == destination.fingerprint
            and state.get("status") in {"pending", "synced", "error"}
        )

    @callback
    def _advertisement(self, service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        if service_info.connectable and not self._paused:
            self._wake.set()

    def _get_device(self):
        return bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)

    def _envelope(
        self,
        readings: dict[str, dict[str, Any]],
        pending: dict[str, dict[str, Any]],
        states: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "version": 2,
            "profiles": readings,
            "pending_sync": pending,
            "sync_state": states,
        }

    def _publish(self) -> None:
        self.async_set_updated_data(deepcopy(self._readings))

    def _profile_claimed(self, profile_id: str) -> None:
        preset = self._preset
        self._claim = (
            preset
            if preset is not None and preset[0] == profile_id
            else (profile_id, METHOD_CONFIRMATION)
        )
        self._refresh_assignment("replaced")

    def _claim_released(self, reason: str) -> None:
        self._claim = None
        self._refresh_assignment(reason)

    async def async_claim_weighing(
        self,
        request_id: str,
        profile_id: str,
        *,
        claimant_user_id: str | None = None,
        context_user_id: str | None = None,
    ) -> None:
        """Claim the active wake request for one profile."""
        if self._paused or not self._settings.get(CONF_REQUIRE_PROFILE_CONFIRMATION, False):
            raise ServiceValidationError("The weighing request is no longer available")
        self._identity.claim(
            request_id,
            profile_id,
            claimant_user_id=claimant_user_id,
            context_user_id=context_user_id,
        )

    def _profile_snapshot(self, profile_id: str) -> dict[str, Any]:
        profile = {key: self._profiles[profile_id][key] for key in PROFILE_KEYS}
        if (reading := self._readings.get(profile_id)) is not None:
            profile["latest_weight_kg"] = reading["weight_kg"]
        return profile

    def _resolve_profile(self, wake_id: int) -> Coroutine[Any, Any, SessionProfile | None]:
        self._async_update_proximity()
        self._preset = None
        if self._selected_profile_id is not None:
            self._preset = (self._selected_profile_id, METHOD_SELECTION)
        elif self._beacon_profile_id is not None:
            self._preset = (self._beacon_profile_id, METHOD_PROXIMITY)
        if self._preset is None:
            future = self._identity.request(wake_id)
        else:
            future = self._identity.request(wake_id, self._preset[0], source=self._preset[1])

        async def resolve() -> SessionProfile | None:
            profile_id = await future
            if profile_id is None:
                return None
            method = (
                self._claim[1]
                if self._claim is not None and self._claim[0] == profile_id
                else METHOD_CONFIRMATION
            )

            async def save(measurement: dict[str, Any]) -> None:
                await self._async_measurement(measurement, profile_id, method=method)
                self._identity.finish(wake_id)

            return SessionProfile(self._profile_snapshot(profile_id), save)

        return resolve()

    def _measurement_event(
        self, profile_id: str, measurement: dict[str, Any], *, sync_status: str | None = None
    ) -> None:
        measurement_id = measurement.get("measurement_id")
        if not isinstance(measurement_id, str) or not measurement_id:
            return
        try:
            parsed_id = UUID(measurement_id)
            if parsed_id.hex != measurement_id or parsed_id.version != 4:
                return
        except ValueError:
            return
        payload = {
            "entry_id": self.entry.entry_id,
            "profile_id": profile_id,
            "measurement_id": measurement_id,
            "weight_kg": measurement["weight_kg"],
            "fat_percent": measurement.get("fat_percent"),
            "impedance_ohm": measurement.get("impedance_ohm"),
            "received_at": measurement["received_at"],
            "sparky_enabled": bool(self._profiles[profile_id].get(CONF_SPARKY_ENABLED, False)),
        }
        if person := self._profiles[profile_id].get(CONF_PERSON):
            payload[CONF_PERSON] = person
        if assigned_by := measurement.get("assigned_by"):
            payload["assigned_by"] = assigned_by
        if redirected_from := measurement.get("redirected_from"):
            payload["redirected_from"] = redirected_from
        if measurement.get("weight_check_overridden"):
            payload["weight_check_overridden"] = True
        event = "measurement_saved"
        if sync_status is not None:
            payload["sync_status"] = sync_status
            event = "measurement_completed"
        self.hass.bus.async_fire(f"{DOMAIN}_{event}", payload)

    async def _commit(
        self,
        readings: dict[str, dict[str, Any]],
        pending: dict[str, dict[str, Any]],
        states: dict[str, dict[str, Any]],
    ) -> None:
        await self._store.async_save(self._envelope(readings, pending, states))
        self._readings, self._pending_sync, self._sync_state = readings, pending, states
        self._publish()

    def _weight_reference(
        self, profile_id: str, *, allow_stale: bool = False
    ) -> WeightReference | None:
        if (reading := self._readings.get(profile_id)) is None:
            return WeightReference(self._profiles[profile_id][CONF_INITIAL_WEIGHT], initial=True)
        received_at = datetime.fromisoformat(reading["received_at"])
        if not allow_stale and dt_util.utcnow() - received_at > REFERENCE_MAX_AGE:
            return None
        return WeightReference(reading["weight_kg"])

    def _reject(
        self, measurement: dict[str, Any], profile_id: str | None, reason: str, **details: Any
    ) -> None:
        _LOGGER.info("Weighing of %s kg discarded (%s)", measurement["weight_kg"], reason)
        self._last_rejection = {
            "rejected_at": dt_util.utcnow().isoformat(),
            "weight_kg": measurement["weight_kg"],
            "reason": reason,
        }
        self.async_update_listeners()
        self.hass.bus.async_fire(
            f"{DOMAIN}_measurement_rejected",
            {
                "entry_id": self.entry.entry_id,
                "profile_id": profile_id,
                "reason": reason,
                "weight_kg": measurement["weight_kg"],
                **details,
                "impedance_ohm": measurement.get("impedance_ohm"),
            },
        )

    def _weight_assignment(
        self, measurement: dict[str, Any], configured_profile_id: str
    ) -> tuple[str | None, dict[str, Any]]:
        if len(self._profiles) == 1:
            return self._checked_profile(measurement, configured_profile_id, verified=False)
        # Without a selection even an old reading is the best hint for whose weight this is.
        references = {
            profile_id: self._weight_reference(profile_id, allow_stale=True)
            for profile_id in self._profiles
        }
        candidates = matching_profiles(measurement["weight_kg"], references)
        if len(candidates) != 1:
            details: dict[str, Any] = {"candidates": candidates}
            if self._crowded_profile_ids:
                details["nearby_profiles"] = self._crowded_profile_ids
            self._reject(
                measurement,
                None,
                "ambiguous_weight" if candidates else "no_matching_profile",
                **details,
            )
            return None, measurement
        if candidates[0] == configured_profile_id:
            return candidates[0], measurement
        return candidates[0], self._without_body_fat(measurement)

    @staticmethod
    def _without_body_fat(measurement: dict[str, Any]) -> dict[str, Any]:
        # The scale derived body fat from the configured profile's height, age, and sex.
        stripped = {key: value for key, value in measurement.items() if key != "fat_raw_tenths"}
        return {**stripped, "fat_percent": None}

    def _checked_profile(
        self, measurement: dict[str, Any], profile_id: str, verified: bool
    ) -> tuple[str | None, dict[str, Any]]:
        if not self._settings.get(CONF_WEIGHT_CHECK, False):
            return profile_id, measurement
        references = {other_id: self._weight_reference(other_id) for other_id in self._profiles}
        outcome, target = check_weight(
            measurement["weight_kg"], profile_id, references, verified=verified
        )
        if outcome is Outcome.ACCEPTED:
            return profile_id, measurement
        if outcome is Outcome.REDIRECTED:
            _LOGGER.info("Weighing did not fit profile %s and was moved to %s", profile_id, target)
            return target, {**self._without_body_fat(measurement), "redirected_from": profile_id}
        reference = references[profile_id]
        self._reject(
            measurement,
            profile_id,
            "weight_mismatch",
            reference_weight_kg=reference.weight_kg if reference else None,
        )
        return None, measurement

    def _overridden_check(self, measurement: dict[str, Any], profile_id: str) -> dict[str, Any]:
        """Keep a directly selected weighing but flag it when it fails the weight check.

        A deliberate selection is the way out when a real weight change or a wrong
        reference would otherwise block every weighing of the profile.
        """
        if not self._settings.get(CONF_WEIGHT_CHECK, False):
            return measurement
        references = {other_id: self._weight_reference(other_id) for other_id in self._profiles}
        outcome, _ = check_weight(measurement["weight_kg"], profile_id, references, verified=False)
        if outcome is Outcome.ACCEPTED:
            return measurement
        _LOGGER.info("Directly selected weighing for %s kept despite the weight check", profile_id)
        return {**measurement, "weight_check_overridden": True}

    async def _async_measurement(
        self,
        measurement: dict[str, Any],
        profile_id: str | None = None,
        *,
        method: str = METHOD_SELECTION,
    ) -> None:
        if profile_id is None and self._settings.get(CONF_REQUIRE_PROFILE_CONFIRMATION, False):
            raise ValueError("measurement profile is required")
        selected_profile_id = profile_id = profile_id or self._default_profile_id
        if profile_id not in self._profiles:
            raise ValueError("measurement profile does not exist")
        if method in (METHOD_WEIGHT, METHOD_SINGLE_PROFILE):
            profile_id, measurement = self._weight_assignment(measurement, profile_id)
        elif method == METHOD_SELECTION:
            measurement = self._overridden_check(measurement, profile_id)
        else:
            profile_id, measurement = self._checked_profile(measurement, profile_id, verified=True)
        if profile_id is None:
            return
        payload = {
            **measurement,
            "assigned_by": method,
            "received_at": dt_util.utcnow().isoformat(),
            "measurement_id": uuid4().hex,
        }
        async with self._storage_lock:
            readings = {**self._readings, profile_id: payload}
            pending, states = dict(self._pending_sync), dict(self._sync_state)
            destination = self._destinations.get(profile_id)
            if destination is not None:
                day = measurement_day(payload, self.hass.config.time_zone)
                pending[f"{profile_id}:{day}"] = {
                    "profile_id": profile_id,
                    "day": day,
                    "destination": destination.fingerprint,
                    "measurement": payload,
                    "attempts": 0,
                    "retry_at": 0,
                }
                states[profile_id] = {"destination": destination.fingerprint, "status": "pending"}
            # Store the reading and its sync job together so a restart cannot split their state.
            await self._commit(readings, pending, states)
        self._measurement_event(profile_id, payload)
        if destination is None and profile_id not in self._destination_errors:
            self._measurement_event(profile_id, payload, sync_status="disabled")
        self._sync_wake.set()
        if method == METHOD_SELECTION and self._selected_profile_id == selected_profile_id:
            self._selected_profile_id = None
            self._refresh_assignment("weighing_saved")

    def _next_session_assignment(self) -> tuple[str, str]:
        self._async_update_proximity()
        if self._selected_profile_id is not None:
            return self._selected_profile_id, METHOD_SELECTION
        if self._beacon_profile_id is not None:
            return self._beacon_profile_id, METHOD_PROXIMITY
        if len(self._profiles) == 1:
            return self._default_profile_id, METHOD_SINGLE_PROFILE
        # The scale needs some profile for body fat; the last person weighed is the best guess.
        latest = max(
            self._readings,
            key=lambda profile_id: datetime.fromisoformat(
                self._readings[profile_id]["received_at"]
            ),
            default=self._default_profile_id,
        )
        return latest, METHOD_WEIGHT

    async def _async_listen(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            if self._paused or self._default_profile_id is None:
                return
            device = self._get_device()
            if device is None:
                continue
            # Keep this session's readings bound to its profile, even if the user switches profiles.
            profile_id, method = self._session_assignment = self._next_session_assignment()
            profile = self._profile_snapshot(profile_id)
            stop_event = self._session_stop = asyncio.Event()
            self._session_done.clear()
            client = None
            delay = 3

            async def save(
                measurement: dict[str, Any],
                selected_profile: str = profile_id,
                selected_method: str = method,
            ) -> None:
                await self._async_measurement(measurement, selected_profile, method=selected_method)

            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    NAME,
                    max_attempts=2,
                    ble_device_callback=self._get_device,
                    timeout=15,
                    use_services_cache=False,
                )
                if not self._paused:
                    identity_options = {}
                    if self._settings.get(CONF_REQUIRE_PROFILE_CONFIRMATION, False):
                        identity_options = {
                            "resolve_profile": self._resolve_profile,
                            "cancel_profile": lambda wake_id, reason: self._identity.cancel(
                                reason, wake_id
                            ),
                            "now_factory": dt_util.now,
                        }
                    await run_session(
                        client,
                        self.address,
                        None if identity_options else profile,
                        save,
                        allow_bind=self._settings.get(CONF_ALLOW_BIND, False),
                        user_id=self._settings.get(CONF_USER_ID, 1),
                        now=dt_util.now(),
                        stop_event=stop_event,
                        **identity_options,
                    )
            except AuthenticationError:
                _LOGGER.warning(
                    "Scale authentication was rejected. Close Huawei Health and review pairing "
                    "in the integration options"
                )
                delay = 300
            except (
                BleakError,
                ConnectionError,
                TimeoutError,
                OSError,
                ValueError,
                RuntimeError,
            ) as err:
                _LOGGER.debug("Scale session ended: %s", type(err).__name__, exc_info=True)
            finally:
                try:
                    if client is not None:
                        with suppress(BleakError, ConnectionError, TimeoutError, OSError):
                            await asyncio.wait_for(client.disconnect(), timeout=10)
                finally:
                    self._session_stop = None
                    self._session_assignment = None
                    self._session_done.set()
            if self._paused:
                return
            await asyncio.sleep(delay)

    async def async_prepare_reload(self) -> None:
        """Pause discovery and wait for the current BLE session to stop."""
        self._paused = True
        self._identity.cancel("reload")
        self._wake.set()
        if self._session_stop is not None:
            self._session_stop.set()
        await self._session_done.wait()

    async def async_select_profile(self, profile_id: str | None) -> None:
        """Select a profile for the next weighing, or None to assign it automatically."""
        if profile_id is not None and profile_id not in self._profiles:
            raise ValueError("selected profile does not exist")
        if profile_id == self._selected_profile_id:
            return
        self._selected_profile_id = profile_id
        self._refresh_assignment("replaced" if profile_id is not None else "deselected")
        if (
            self._session_stop is not None
            and not self._settings.get(CONF_REQUIRE_PROFILE_CONFIRMATION, False)
            and self._session_assignment is not None
            and self._session_assignment != self._next_session_assignment()
        ):
            # The running session configured the scale for another profile; reconnect after it.
            self._session_stop.set()

    async def _async_sync(self) -> None:
        while True:
            self._sync_wake.clear()
            now = dt_util.utcnow().timestamp()
            ready = next(
                (
                    (key, deepcopy(record))
                    for key, record in self._pending_sync.items()
                    if record.get("retry_at", 0) <= now
                ),
                None,
            )
            if ready is None:
                delay = min(
                    (
                        max(0, record.get("retry_at", 0) - now)
                        for record in self._pending_sync.values()
                    ),
                    default=None,
                )
                try:
                    await asyncio.wait_for(self._sync_wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
                continue
            key, record = ready
            destination = self._destinations.get(record["profile_id"])
            # A queued reading stays with the account that was configured when it was recorded.
            if destination is None or destination.fingerprint != record["destination"]:
                continue
            success = False
            try:
                await self._sparky.async_send(
                    destination,
                    record["measurement"],
                    self.hass.config.time_zone,
                    day=record["day"],
                )
                success = True
            except Exception as err:
                _LOGGER.debug("SparkyFitness sync failed: %s", type(err).__name__)
            try:
                await self._async_sync_result(key, record, success)
            except (OSError, ValueError, RuntimeError) as err:
                _LOGGER.debug("SparkyFitness sync state could not be saved: %s", type(err).__name__)
                try:
                    await asyncio.wait_for(self._sync_wake.wait(), timeout=SYNC_RETRY_SECONDS)
                except TimeoutError:
                    pass

    async def _async_sync_result(self, key: str, record: dict[str, Any], success: bool) -> None:
        async with self._storage_lock:
            if self._pending_sync.get(key) != record:
                # A newer reading for this profile and day may have replaced this in-flight job.
                return
            pending, states = dict(self._pending_sync), dict(self._sync_state)
            profile_id = record["profile_id"]
            if success:
                del pending[key]
                status = (
                    "pending"
                    if any(item["profile_id"] == profile_id for item in pending.values())
                    else "synced"
                )
            else:
                attempts = record.get("attempts", 0) + 1
                retry_delay = min(
                    SYNC_RETRY_SECONDS * 2 ** min(attempts - 1, 10), SYNC_MAX_RETRY_SECONDS
                )
                pending[key] = {
                    **record,
                    "attempts": attempts,
                    "retry_at": (dt_util.utcnow() + timedelta(seconds=retry_delay)).timestamp(),
                }
                status = "error"
            states[profile_id] = {"destination": record["destination"], "status": status}
            await self._commit(dict(self._readings), pending, states)
        if success:
            self._measurement_event(profile_id, record["measurement"], sync_status="synced")

    async def async_stop(self) -> None:
        """Invalidate pending claims and stop the BLE and sync workers."""
        self._paused = True
        self._identity.cancel("stopped")
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_proximity is not None:
            self._unsubscribe_proximity()
            self._unsubscribe_proximity = None
        if self._proximity is not None:
            self._proximity.async_stop()
        tasks = [task for task in (self._task, self._sync_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = self._sync_task = None
