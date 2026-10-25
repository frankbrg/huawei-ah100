from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from datetime import datetime
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_ALLOW_BIND, CONF_USER_ID, DOMAIN, NAME, PROFILE_KEYS
from .session import AuthenticationError, run_session
from .storage import MeasurementStore

_LOGGER = logging.getLogger(__name__)


class HuaweiScaleCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinate Bluetooth sessions and the latest stored scale measurement."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=NAME, config_entry=entry)
        self.entry = entry
        self.address = entry.data[CONF_ADDRESS]
        self._settings = {**entry.data, **entry.options}
        self._store = MeasurementStore(
            hass, hass.config.path(".storage", f"{DOMAIN}.{entry.entry_id}.json")
        )
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._unsubscribe = None
        self.data = None

    async def async_start(self) -> None:
        """Restore the latest reading and start passive Bluetooth listening."""
        stored = await self._store.async_load()
        if self._valid_stored_measurement(stored):
            self.async_set_updated_data(stored)
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

    def _valid_stored_measurement(self, stored: Any) -> bool:
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
            and stored.get("user_id") == self._settings.get(CONF_USER_ID, 1)
            and received_at.utcoffset() is not None
        )

    @callback
    def _advertisement(self, service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        if service_info.connectable:
            self._wake.set()

    def _get_device(self):
        return bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)

    async def _async_measurement(self, measurement: dict[str, Any]) -> None:
        payload = {**measurement, "received_at": dt_util.utcnow().isoformat()}
        # Persist before returning; the session sends its BLE acknowledgement after this callback.
        await self._store.async_save(payload)
        self.async_set_updated_data(payload)

    async def _async_listen(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            device = self._get_device()
            if device is None:
                continue
            client = None
            delay = 3
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    NAME,
                    max_attempts=2,
                    ble_device_callback=self._get_device,
                    timeout=15,
                )
                profile = {key: self._settings[key] for key in PROFILE_KEYS}
                if self.data is not None:
                    profile["latest_weight_kg"] = self.data["weight_kg"]
                await run_session(
                    client,
                    self.address,
                    profile,
                    self._async_measurement,
                    allow_bind=self._settings.get(CONF_ALLOW_BIND, False),
                    user_id=self._settings.get(CONF_USER_ID, 1),
                    now=dt_util.now(),
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
                _LOGGER.debug("Scale session ended: %s", type(err).__name__)
            finally:
                if client is not None:
                    with suppress(BleakError, ConnectionError, TimeoutError, OSError):
                        await asyncio.wait_for(client.disconnect(), timeout=10)
            await asyncio.sleep(delay)

    async def async_stop(self) -> None:
        """Stop listening and release the Bluetooth advertisement callback."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
