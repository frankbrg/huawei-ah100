from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakSlotManager
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager
from homeassistant.core import HomeAssistant

from custom_components.huawei_ah100 import proximity
from custom_components.huawei_ah100.proximity import (
    PhoneProximity,
    normalize_beacon_uuid,
    parse_ibeacon_uuid,
)

SCALE = "AA:BB:CC:DD:EE:FF"
ALICE_UUID = "e2c56db5-dffb-48d2-b060-d0f5a71096e0"
BOB_UUID = "74278bda-b644-4520-8f0c-720eaf059935"
ALICE_PHONE = "11:22:33:44:55:01"
BOB_PHONE = "11:22:33:44:55:02"
NOW = 1000.0


def _ibeacon(uuid: str, prefix: bytes = b"\x02\x15") -> dict[int, bytes]:
    return {0x004C: prefix + UUID(uuid).bytes + b"\x00\x01\x00\x02\xc5"}


class FakeRadio:
    """Per-scanner advertisement cache as exposed by the HA Bluetooth manager."""

    def __init__(self) -> None:
        self.seen: dict[str, dict[str, tuple[int, float]]] = {}

    def see(self, source: str, address: str, rssi: int, age: float = 0.0) -> None:
        self.seen.setdefault(source, {})[address] = (rssi, NOW - age)

    def devices(self, _hass: HomeAssistant, address: str, connectable: bool):
        assert connectable is False
        return [
            SimpleNamespace(
                scanner=SimpleNamespace(
                    source=source,
                    discovered_device_timestamps={
                        seen_address: seen_at for seen_address, (_, seen_at) in entries.items()
                    },
                ),
                advertisement=SimpleNamespace(rssi=entries[address][0]),
            )
            for source, entries in self.seen.items()
            if address in entries
        ]


@pytest.fixture
def radio():
    fake = FakeRadio()
    with (
        patch.object(proximity.bluetooth, "async_scanner_devices_by_address", fake.devices),
        patch.object(proximity.bluetooth, "MONOTONIC_TIME", return_value=NOW),
    ):
        yield fake


def _tracker() -> PhoneProximity:
    tracker = PhoneProximity(Mock(), SCALE, {ALICE_UUID: "alice", BOB_UUID: "bob"})
    for address, uuid in ((ALICE_PHONE, ALICE_UUID), (BOB_PHONE, BOB_UUID)):
        tracker._advertisement(
            SimpleNamespace(address=address, manufacturer_data=_ibeacon(uuid)), None
        )
    return tracker


def test_parse_ibeacon_uuid() -> None:
    assert parse_ibeacon_uuid(_ibeacon(ALICE_UUID)) == ALICE_UUID
    assert parse_ibeacon_uuid(_ibeacon(ALICE_UUID, prefix=b"\x10\x05")) is None
    assert parse_ibeacon_uuid({0x004C: b"\x02\x15\x00"}) is None
    assert parse_ibeacon_uuid({0x0075: _ibeacon(ALICE_UUID)[0x004C]}) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ALICE_UUID.upper(), ALICE_UUID),
        (ALICE_UUID.replace("-", ""), ALICE_UUID),
        ("not-a-uuid", None),
        (None, None),
    ],
)
def test_normalize_beacon_uuid(value: object, expected: str | None) -> None:
    assert normalize_beacon_uuid(value) == expected


def test_single_nearby_phone_at_the_scale_scanner_selects_its_profile(radio: FakeRadio) -> None:
    radio.see("bath", SCALE, -55)
    radio.see("hall", SCALE, -90)
    radio.see("bath", ALICE_PHONE, -60)
    radio.see("hall", BOB_PHONE, -40)

    tracker = _tracker()

    assert tracker.rssi_by_profile() == {"alice": -60}
    assert tracker.nearby_profile(-65) == "alice"
    assert tracker.nearby_profile(-59) is None


def test_two_nearby_phones_are_ambiguous(radio: FakeRadio) -> None:
    radio.see("bath", SCALE, -55)
    radio.see("bath", ALICE_PHONE, -60)
    radio.see("bath", BOB_PHONE, -62)

    assert _tracker().nearby_profile(-65) is None


def test_stale_phone_reading_is_ignored(radio: FakeRadio) -> None:
    radio.see("bath", SCALE, -55)
    radio.see("bath", ALICE_PHONE, -60, age=proximity.PROXIMITY_WINDOW_SECONDS + 1)
    radio.see("bath", BOB_PHONE, -62)

    assert _tracker().nearby_profile(-65) == "bob"


def test_unknown_scale_scanner_selects_nothing(radio: FakeRadio) -> None:
    radio.see("bath", ALICE_PHONE, -60)

    assert _tracker().nearby_profile(-65) is None


def test_rotated_address_is_forgotten_and_new_address_learned(radio: FakeRadio) -> None:
    radio.see("bath", SCALE, -55)
    radio.see("bath", ALICE_PHONE, -60)
    tracker = _tracker()

    del radio.seen["bath"][ALICE_PHONE]
    rotated = "11:22:33:44:55:99"
    radio.see("bath", rotated, -58)
    tracker._advertisement(
        SimpleNamespace(address=rotated, manufacturer_data=_ibeacon(ALICE_UUID)), None
    )

    assert tracker.rssi_by_profile() == {"alice": -58}
    assert ALICE_PHONE not in tracker._uuid_by_address


def test_address_that_stops_sending_a_configured_beacon_is_dropped(radio: FakeRadio) -> None:
    radio.see("bath", SCALE, -55)
    radio.see("bath", ALICE_PHONE, -60)
    tracker = _tracker()

    tracker._advertisement(
        SimpleNamespace(
            address=ALICE_PHONE, manufacturer_data=_ibeacon(BOB_UUID.replace("7", "8"))
        ),
        None,
    )

    assert tracker.rssi_by_profile() == {}


def test_start_registers_passive_apple_callback_and_stop_unregisters() -> None:
    unsubscribe = Mock()
    tracker = PhoneProximity(Mock(), SCALE, {ALICE_UUID: "alice"})
    with patch.object(
        proximity.bluetooth, "async_register_callback", return_value=unsubscribe
    ) as register:
        tracker.async_start()

    _, _, matcher, mode = register.call_args.args
    assert matcher == {"manufacturer_id": 0x004C, "connectable": False}
    assert mode is proximity.bluetooth.BluetoothScanningMode.PASSIVE
    tracker.async_stop()
    tracker.async_stop()
    unsubscribe.assert_called_once_with()


async def test_real_manager_delivers_non_connectable_ibeacons(tmp_path) -> None:
    hass = HomeAssistant(str(tmp_path))
    matcher = Mock()
    matcher.match_domains.return_value = set()
    manager = HomeAssistantBluetoothManager(hass, matcher, Mock(), Mock(), BleakSlotManager())
    tracker = PhoneProximity(hass, SCALE, {ALICE_UUID: "alice"})

    def advertisement(address: str, uuid: str) -> BluetoothServiceInfoBleak:
        return BluetoothServiceInfoBleak(
            name="phone",
            address=address,
            rssi=-60,
            manufacturer_data=_ibeacon(uuid),
            service_data={},
            service_uuids=[],
            source="bath",
            device=BLEDevice(address, "phone", {}),
            advertisement=None,
            connectable=False,
            time=hass.loop.time(),
            tx_power=None,
        )

    try:
        with patch("homeassistant.components.bluetooth.api._get_manager", return_value=manager):
            tracker.async_start()
            manager.scanner_adv_received(advertisement(ALICE_PHONE, ALICE_UUID))
            manager.scanner_adv_received(advertisement(BOB_PHONE, BOB_UUID))
            tracker.async_stop()
        assert tracker._uuid_by_address == {ALICE_PHONE: ALICE_UUID}
    finally:
        manager.async_stop()
        await hass.async_stop(force=True)


def test_several_beacons_of_one_profile_count_once_with_the_strongest_signal(
    radio: FakeRadio,
) -> None:
    tag = "11:22:33:44:55:03"
    radio.see("bath", SCALE, -55)
    radio.see("bath", ALICE_PHONE, -70)
    radio.see("bath", tag, -58)
    tracker = PhoneProximity(Mock(), SCALE, {ALICE_UUID: "alice", BOB_UUID: "alice"})
    for address, uuid in ((ALICE_PHONE, ALICE_UUID), (tag, BOB_UUID)):
        tracker._advertisement(
            SimpleNamespace(address=address, manufacturer_data=_ibeacon(uuid)), None
        )

    assert tracker.rssi_by_profile() == {"alice": -58}
    assert tracker.nearby_profile(-65) == "alice"
