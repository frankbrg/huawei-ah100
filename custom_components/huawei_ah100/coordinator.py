from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

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
from .session import AuthenticationError, run_session
from .sparky import SparkyClient, destination_from_profile, measurement_day
from .storage import MeasurementStore

_LOGGER = logging.getLogger(__name__)
SYNC_RETRY_SECONDS = 5
SYNC_MAX_RETRY_SECONDS = 300


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
        requested_profile = entry.options.get(CONF_ACTIVE_PROFILE)
        self._active_profile_id = (
            requested_profile
            if requested_profile in self._profiles
            else self._legacy_profile_id or next(iter(self._profiles), None)
        )
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
        self._selection_lock = asyncio.Lock()
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
        self.data = {}

    @property
    def profiles(self) -> Mapping[str, Mapping[str, Any]]:
        return self._profiles

    @property
    def active_profile_id(self) -> str | None:
        return self._active_profile_id

    def profile_data(self, profile_id: str) -> dict[str, Any] | None:
        reading = self._readings.get(profile_id)
        return dict(reading) if reading is not None else None

    def sync_status(self, profile_id: str) -> str:
        if profile_id in self._destination_errors:
            return "error"
        if self._destinations.get(profile_id) is None:
            return "disabled"
        return self._sync_state.get(profile_id, {}).get("status", "pending")

    async def async_start(self) -> None:
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

    async def _commit(
        self,
        readings: dict[str, dict[str, Any]],
        pending: dict[str, dict[str, Any]],
        states: dict[str, dict[str, Any]],
    ) -> None:
        await self._store.async_save(self._envelope(readings, pending, states))
        self._readings, self._pending_sync, self._sync_state = readings, pending, states
        self._publish()

    async def _async_measurement(
        self, measurement: dict[str, Any], profile_id: str | None = None
    ) -> None:
        profile_id = profile_id or self._active_profile_id
        if profile_id not in self._profiles:
            raise ValueError("measurement profile does not exist")
        payload = {**measurement, "received_at": dt_util.utcnow().isoformat()}
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
        self._sync_wake.set()

    async def _async_listen(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            if self._paused or self._active_profile_id is None:
                return
            device = self._get_device()
            if device is None:
                continue
            # Keep this session's readings bound to its profile, even if the user switches profiles.
            profile_id = self._active_profile_id
            profile = {key: self._profiles[profile_id][key] for key in PROFILE_KEYS}
            if (reading := self._readings.get(profile_id)) is not None:
                profile["latest_weight_kg"] = reading["weight_kg"]
            stop_event = self._session_stop = asyncio.Event()
            self._session_done.clear()
            client = None
            delay = 3

            async def save(measurement: dict[str, Any], selected_profile: str = profile_id) -> None:
                await self._async_measurement(measurement, selected_profile)

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
                    await run_session(
                        client,
                        self.address,
                        profile,
                        save,
                        allow_bind=self._settings.get(CONF_ALLOW_BIND, False),
                        user_id=self._settings.get(CONF_USER_ID, 1),
                        now=dt_util.now(),
                        stop_event=stop_event,
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
                    self._session_done.set()
            if self._paused:
                return
            await asyncio.sleep(delay)

    async def async_prepare_reload(self) -> None:
        self._paused = True
        self._wake.set()
        if self._session_stop is not None:
            self._session_stop.set()
        await self._session_done.wait()

    async def async_select_profile(self, profile_id: str) -> None:
        if profile_id not in self._profiles:
            raise ValueError("selected profile does not exist")
        async with self._selection_lock:
            current = self.entry.options.get(CONF_ACTIVE_PROFILE, self._active_profile_id)
            if current not in self._profiles:
                current = self._active_profile_id
            if profile_id == current:
                return
            await self.async_prepare_reload()
            self.hass.config_entries.async_update_entry(
                self.entry, options={**self.entry.options, CONF_ACTIVE_PROFILE: profile_id}
            )

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

    async def async_stop(self) -> None:
        self._paused = True
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = [task for task in (self._task, self._sync_task) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = self._sync_task = None
