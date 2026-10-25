import asyncio
from threading import Event
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakSlotManager
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.huawei_ah100 import protocol, session
from custom_components.huawei_ah100.coordinator import HuaweiScaleCoordinator

PROFILE = {
    "address": "AA:BB:CC:DD:EE:FF",
    "birth_date": "2000-08-20",
    "height_cm": 180,
    "sex": "female",
    "initial_weight_kg": 80.0,
    "user_id": 1,
    "allow_bind": False,
}
MEASUREMENT = {
    "weight_kg": 70.0,
    "fat_percent": 20.0,
    "impedance_ohm": 450,
    "user_id": 1,
    "measured_at": "2026-01-01T09:15:00",
    "unknown_tail_hex": "14",
}


@pytest.fixture
async def coordinator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = ConfigEntry(
        version=1,
        minor_version=1,
        domain="huawei_ah100",
        title="Huawei AH100",
        data=PROFILE,
        options={},
        source="user",
        unique_id=PROFILE["address"],
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
    )
    result = HuaweiScaleCoordinator(hass, entry)
    yield result
    await result.async_stop()
    await hass.async_stop(force=True)


async def test_durable_save_happens_before_sensor_update(coordinator):
    order = []

    async def save(payload):
        order.append("save")
        assert payload["weight_kg"] == 70.0
        assert payload["received_at"].endswith("+00:00")

    coordinator._store.async_save = save
    with patch.object(
        coordinator, "async_set_updated_data", side_effect=lambda _: order.append("sensor")
    ):
        await coordinator._async_measurement(MEASUREMENT)
    assert order == ["save", "sensor"]


async def test_failed_storage_keeps_previous_actual_measurement(coordinator):
    coordinator.data = {**MEASUREMENT, "weight_kg": 69.5}
    coordinator._store.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    with pytest.raises(OSError):
        await coordinator._async_measurement(MEASUREMENT)
    assert coordinator.data["weight_kg"] == 69.5


async def test_actual_atomic_write_error_does_not_publish_or_replace_data(coordinator):
    await coordinator._async_measurement(MEASUREMENT)
    original = coordinator._store._path.read_bytes()
    with patch("custom_components.huawei_ah100.storage.os.replace", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            await coordinator._async_measurement({**MEASUREMENT, "weight_kg": 71.0})
    assert coordinator.data["weight_kg"] == 70.0
    assert coordinator._store._path.read_bytes() == original
    assert coordinator._store._path.stat().st_mode & 0o777 == 0o600
    assert list(coordinator._store._path.parent.iterdir()) == [coordinator._store._path]


async def test_restore_requires_actual_same_user_data_and_valid_timestamp(coordinator):
    valid = {**MEASUREMENT, "received_at": "2026-01-01T08:15:00+00:00"}
    assert coordinator._valid_stored_measurement(valid)
    for invalid in (
        PROFILE,
        {**valid, "user_id": 2},
        {**valid, "received_at": "invalid"},
        {**valid, "received_at": "2026-01-01T08:15:00"},
        {**valid, "weight_kg": float("nan")},
    ):
        assert not coordinator._valid_stored_measurement(invalid)
    await coordinator._async_measurement(MEASUREMENT)
    stored = await coordinator._store.async_load()
    assert stored["weight_kg"] == 70.0
    assert coordinator.data["weight_kg"] == 70.0


async def test_unload_cancels_listener_and_unsubscribes(coordinator):
    coordinator._unsubscribe = Mock()
    unsubscribe = coordinator._unsubscribe
    stopped = asyncio.Event()

    async def listener():
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    coordinator._task = asyncio.create_task(listener())
    await asyncio.sleep(0)
    await coordinator.async_stop()
    assert stopped.is_set()
    unsubscribe.assert_called_once()


async def test_identical_advertisements_wake_listener_and_unload_removes_observer(coordinator):
    matcher = Mock()
    matcher.match_domains.return_value = set()
    manager = HomeAssistantBluetoothManager(
        coordinator.hass, matcher, Mock(), Mock(), BleakSlotManager()
    )
    advertisement = BluetoothServiceInfoBleak(
        name="CH100 scale",
        address=coordinator.address,
        rssi=-60,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="test",
        device=BLEDevice(coordinator.address, "CH100 scale", {}),
        advertisement=None,
        connectable=True,
        time=coordinator.hass.loop.time(),
        tx_power=None,
    )
    try:
        with (
            patch("homeassistant.components.bluetooth.api._get_manager", return_value=manager),
            patch.object(coordinator, "_async_listen", new=AsyncMock()),
        ):
            await coordinator.async_start()
            assert not coordinator._wake.is_set()
            for _ in range(2):
                manager.scanner_adv_received(advertisement)
                assert coordinator._wake.is_set()
                coordinator._wake.clear()
            await coordinator.async_stop()
            manager.scanner_adv_received(advertisement)
            assert not coordinator._wake.is_set()
    finally:
        manager.async_stop()


async def test_disconnect_preserves_pending_durable_save_with_partial_service_cache(
    coordinator, monkeypatch
):
    monkeypatch.setattr(session, "SLEEP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(session, "DISCONNECT_POLL_SECONDS", 0.001)
    mac = bytes.fromhex(coordinator.address.replace(":", ""))
    client = Mock(is_connected=True)
    full_service_tree = {session.TX_UUID, session.RX_UUID}
    partial_cached_tree = {"00002a19-0000-1000-8000-00805f9b34fb"}

    def get_characteristic(uuid):
        return (
            SimpleNamespace(uuid=uuid, properties=("write", "notify"))
            if uuid in available_services
            else None
        )

    available_services = partial_cached_tree
    client.services.get_characteristic.side_effect = get_characteristic
    subscribed = asyncio.Event()
    configured = asyncio.Event()
    save_entered = asyncio.Event()
    save_release = asyncio.Event()
    save_cancelled = asyncio.Event()
    published = asyncio.Event()
    disconnect_callback = None
    notify_callback = None
    writes = []

    def emit(opcode, payload=b"", *, encrypted=False):
        frame = bytes((0xBC if encrypted else 0xBD, len(payload) + 1, opcode))
        notify_callback(None, bytearray(frame + protocol.obfuscate(payload, mac)))

    async def start_notify(_characteristic, callback):
        nonlocal notify_callback
        notify_callback = callback
        subscribed.set()

    async def write(_characteristic, frame, *, response):
        writes.append(frame)
        if frame[2] == 0x24:
            emit(0x26, b"\x01")
        elif frame[2] == 0x09:
            configured.set()

    async def establish(*_args, **kwargs):
        nonlocal available_services, disconnect_callback
        disconnect_callback = kwargs.get("disconnected_callback")
        available_services = (
            partial_cached_tree
            if kwargs.get("use_services_cache", True)
            else full_service_tree
        )
        return client

    async def save(_payload):
        save_entered.set()
        try:
            await save_release.wait()
        except asyncio.CancelledError:
            save_cancelled.set()
            raise

    client.start_notify = start_notify
    client.write_gatt_char = write
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    coordinator._store.async_save = save
    remove_listener = coordinator.async_add_listener(published.set)
    with (
        patch.object(coordinator, "_get_device", return_value=Mock()),
        patch("custom_components.huawei_ah100.coordinator.establish_connection", establish),
    ):
        coordinator._task = asyncio.create_task(coordinator._async_listen())
        try:
            coordinator._wake.set()
            await asyncio.wait_for(subscribed.wait(), 1)
            emit(0x00)
            await asyncio.wait_for(configured.wait(), 1)
            plaintext = (
                b"\x01"
                + (700).to_bytes(2, "little")
                + (200).to_bytes(2, "little")
                + (2026).to_bytes(2, "little")
                + bytes((1, 1, 9, 15, 0, 4))
                + (450).to_bytes(2, "little")
            )
            emit(
                0x0E,
                protocol.aes_ctr(plaintext, protocol.derive_session_key(protocol.auth_token(1), mac)),
                encrypted=True,
            )
            await asyncio.wait_for(save_entered.wait(), 1)
            client.is_connected = False
            if disconnect_callback is not None:
                disconnect_callback(client)
            await asyncio.sleep(0.03)
            assert not save_cancelled.is_set()
            assert coordinator.data is None
            save_release.set()
            await asyncio.wait_for(published.wait(), 1)
            assert coordinator.data["weight_kg"] == 70.0
            assert not any(frame[2] == 0x13 for frame in writes)
        finally:
            save_release.set()
            await coordinator.async_stop()
            remove_listener()


async def test_unload_waits_for_atomic_write_before_a_new_store_can_save(coordinator):
    entered = Event()
    release = Event()
    finished = Event()
    original_save = coordinator._store._save

    def blocked_save(payload):
        entered.set()
        try:
            if not release.wait(2):
                raise TimeoutError("test did not release the storage write")
            original_save(payload)
        finally:
            finished.set()

    with patch.object(coordinator._store, "_save", blocked_save):
        coordinator._task = asyncio.create_task(coordinator._async_measurement(MEASUREMENT))
        stop = None
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            stop = asyncio.create_task(coordinator.async_stop())
            await asyncio.sleep(0.01)
            assert not stop.done()
            release.set()
            await asyncio.wait_for(stop, 1)
            assert finished.is_set()
            new_store = type(coordinator._store)(
                coordinator.hass, str(coordinator._store._path)
            )
            await new_store.async_save({**MEASUREMENT, "weight_kg": 71.0})
            assert (await new_store.async_load())["weight_kg"] == 71.0
        finally:
            release.set()
            if stop is not None:
                await asyncio.gather(stop, return_exceptions=True)
            await asyncio.to_thread(finished.wait, 1)
