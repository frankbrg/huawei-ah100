"""Pick the profile whose phone iBeacon is close to the scale."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback

APPLE_MANUFACTURER_ID = 0x004C
IBEACON_PREFIX = b"\x02\x15"
PROXIMITY_WINDOW_SECONDS = 10.0


def normalize_beacon_uuid(value: Any) -> str | None:
    """Return the canonical lowercase form of an iBeacon UUID."""
    if not isinstance(value, str):
        return None
    try:
        return str(UUID(value.strip()))
    except ValueError:
        return None


def parse_ibeacon_uuid(manufacturer_data: Mapping[int, bytes]) -> str | None:
    """Return the iBeacon UUID carried in Apple manufacturer data."""
    data = manufacturer_data.get(APPLE_MANUFACTURER_ID)
    if data is None or len(data) < 23 or data[:2] != IBEACON_PREFIX:
        return None
    return str(UUID(bytes=bytes(data[2:18])))


class PhoneProximity:
    """Track phone iBeacons and read their signal at the scale's scanner."""

    def __init__(self, hass: HomeAssistant, scale_address: str, beacons: Mapping[str, str]) -> None:
        self._hass = hass
        self._scale_address = scale_address
        self._profiles_by_uuid = dict(beacons)
        # Android rotates the advertising address, so the UUID is the identity.
        self._uuid_by_address: dict[str, str] = {}
        self._unsubscribe: Callable[[], None] | None = None

    @callback
    def async_start(self) -> None:
        """Learn which addresses currently advertise a configured UUID."""
        self._unsubscribe = bluetooth.async_register_callback(
            self._hass,
            self._advertisement,
            {"manufacturer_id": APPLE_MANUFACTURER_ID, "connectable": False},
            bluetooth.BluetoothScanningMode.PASSIVE,
        )

    @callback
    def async_stop(self) -> None:
        """Stop learning beacon addresses."""
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    @callback
    def _advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, _change: Any
    ) -> None:
        uuid = parse_ibeacon_uuid(service_info.manufacturer_data)
        if uuid in self._profiles_by_uuid:
            self._uuid_by_address[service_info.address] = uuid
        else:
            self._uuid_by_address.pop(service_info.address, None)

    def _scale_source(self) -> str | None:
        devices = bluetooth.async_scanner_devices_by_address(
            self._hass, self._scale_address, connectable=False
        )
        best = max(devices, key=lambda device: device.advertisement.rssi, default=None)
        return best.scanner.source if best is not None else None

    def rssi_by_profile(self) -> dict[str, int]:
        """Return each profile phone's recent RSSI at the scanner closest to the scale."""
        if (source := self._scale_source()) is None:
            return {}
        now = bluetooth.MONOTONIC_TIME()
        timestamps: dict[str, Mapping[str, float]] = {}
        result: dict[str, int] = {}
        for address, uuid in list(self._uuid_by_address.items()):
            devices = bluetooth.async_scanner_devices_by_address(
                self._hass, address, connectable=False
            )
            if not devices:
                del self._uuid_by_address[address]
                continue
            for device in devices:
                scanner = device.scanner
                if scanner.source != source:
                    continue
                if source not in timestamps:
                    timestamps[source] = scanner.discovered_device_timestamps
                seen_at = timestamps[source].get(address)
                if seen_at is None or now - seen_at > PROXIMITY_WINDOW_SECONDS:
                    continue
                profile_id = self._profiles_by_uuid[uuid]
                rssi = device.advertisement.rssi
                result[profile_id] = max(rssi, result.get(profile_id, rssi))
        return result

    def nearby_profile(self, threshold: int) -> str | None:
        """Return the only profile with a phone at or above the threshold."""
        nearby = [
            profile_id for profile_id, rssi in self.rssi_by_profile().items() if rssi >= threshold
        ]
        return nearby[0] if len(nearby) == 1 else None
