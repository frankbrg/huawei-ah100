import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
from threading import Event
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakSlotManager
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.manager import HomeAssistantBluetoothManager
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from custom_components.huawei_ah100 import protocol, session
from custom_components.huawei_ah100.coordinator import HuaweiScaleCoordinator
from custom_components.huawei_ah100.select import HuaweiAH100ActiveProfileSelect

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


def make_entry(*, profiles=None, options=None):
    return ConfigEntry(
        version=2,
        minor_version=1,
        domain="huawei_ah100",
        title="Huawei AH100",
        data=PROFILE,
        options=options or {},
        source="user",
        unique_id=PROFILE["address"],
        discovery_keys=MappingProxyType({}),
        subentries_data=[
            {
                "subentry_id": profile_id,
                "subentry_type": "profile",
                "title": profile_id.title(),
                "unique_id": "primary" if profile_id == "alice" else None,
                "data": data,
            }
            for profile_id, data in (
                profiles
                if profiles is not None
                else {
                    "alice": {**PROFILE, "legacy_profile": True},
                    "bob": {**PROFILE, "initial_weight_kg": 65.0},
                }
            ).items()
        ],
    )


@pytest.fixture
async def coordinator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    result = HuaweiScaleCoordinator(hass, make_entry())
    yield result
    await result.async_stop()
    await hass.async_stop(force=True)


async def test_durable_save_happens_before_sensor_update(coordinator):
    order = []

    async def save(payload):
        order.append("save")
        assert payload["profiles"]["alice"]["weight_kg"] == 70.0
        assert payload["profiles"]["alice"]["received_at"].endswith("+00:00")

    coordinator._store.async_save = save
    with patch.object(
        coordinator, "async_set_updated_data", side_effect=lambda _: order.append("sensor")
    ):
        await coordinator._async_measurement(MEASUREMENT)
    assert order == ["save", "sensor"]


async def test_failed_storage_keeps_previous_actual_measurement(coordinator):
    await coordinator._async_measurement({**MEASUREMENT, "weight_kg": 69.5})
    coordinator._store.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    with pytest.raises(OSError):
        await coordinator._async_measurement(MEASUREMENT)
    assert coordinator.profile_data("alice")["weight_kg"] == 69.5


async def test_actual_atomic_write_error_does_not_publish_or_replace_data(coordinator):
    await coordinator._async_measurement(MEASUREMENT)
    original = coordinator._store._path.read_bytes()
    with patch("custom_components.huawei_ah100.storage.os.replace", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            await coordinator._async_measurement({**MEASUREMENT, "weight_kg": 71.0})
    assert coordinator.profile_data("alice")["weight_kg"] == 70.0
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
    assert stored["profiles"]["alice"]["weight_kg"] == 70.0
    assert coordinator.profile_data("alice")["weight_kg"] == 70.0


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
            partial_cached_tree if kwargs.get("use_services_cache", True) else full_service_tree
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
                protocol.aes_ctr(
                    plaintext, protocol.derive_session_key(protocol.auth_token(1), mac)
                ),
                encrypted=True,
            )
            await asyncio.wait_for(save_entered.wait(), 1)
            client.is_connected = False
            if disconnect_callback is not None:
                disconnect_callback(client)
            await asyncio.sleep(0.03)
            assert not save_cancelled.is_set()
            assert coordinator.data == {}
            save_release.set()
            await asyncio.wait_for(published.wait(), 1)
            assert coordinator.profile_data("alice")["weight_kg"] == 70.0
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
            new_store = type(coordinator._store)(coordinator.hass, str(coordinator._store._path))
            await new_store.async_save({**MEASUREMENT, "weight_kg": 71.0})
            assert (await new_store.async_load())["weight_kg"] == 71.0
        finally:
            release.set()
            if stop is not None:
                await asyncio.gather(stop, return_exceptions=True)
            await asyncio.to_thread(finished.wait, 1)


async def start_without_bluetooth(coordinator):
    with (
        patch.object(coordinator, "_get_device", return_value=None),
        patch(
            "custom_components.huawei_ah100.coordinator.bluetooth.async_register_advertisement_callback",
            return_value=Mock(),
        ),
    ):
        await coordinator.async_start()


async def wait_until(predicate):
    async with asyncio.timeout(1):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.fixture
async def sync_coordinator(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    entry = make_entry(
        profiles={
            "alice": {
                **PROFILE,
                "legacy_profile": True,
                "sparky_enabled": True,
                "sparky_url": "https://fitness.example.test",
                "sparky_api_key": "synthetic-alice-key",
            },
            "bob": {**PROFILE, "initial_weight_kg": 65.0},
        }
    )
    result = HuaweiScaleCoordinator(hass, entry)
    result._sparky.async_send = AsyncMock()
    yield result
    await result.async_stop()
    await hass.async_stop(force=True)


async def test_profiles_are_isolated_and_configuration_is_a_snapshot(coordinator):
    await coordinator._async_measurement(MEASUREMENT, "alice")
    assert coordinator.profile_data("bob") is None
    await coordinator._async_measurement({**MEASUREMENT, "weight_kg": 55.5}, "bob")
    assert coordinator.profile_data("alice")["weight_kg"] == 70.0
    assert coordinator.profile_data("bob")["weight_kg"] == 55.5
    assert coordinator.sync_status("alice") == "disabled"
    assert coordinator.profiles["alice"]["name"] == "Alice"
    with pytest.raises(TypeError):
        coordinator.profiles["alice"]["height_cm"] = 100
    copied = coordinator.profile_data("alice")
    copied["weight_kg"] = 123.0
    assert coordinator.profile_data("alice")["weight_kg"] == 70.0
    await start_without_bluetooth(coordinator)
    assert coordinator.profile_data("alice")["weight_kg"] == 70.0
    assert coordinator.profile_data("bob")["weight_kg"] == 55.5


async def test_active_profile_uses_valid_option_then_legacy_then_first(coordinator):
    hass = coordinator.hass
    assert coordinator.active_profile_id == "alice"
    assert (
        HuaweiScaleCoordinator(
            hass, make_entry(options={"active_profile_id": "bob"})
        ).active_profile_id
        == "bob"
    )
    assert (
        HuaweiScaleCoordinator(
            hass, make_entry(options={"active_profile_id": "removed"})
        ).active_profile_id
        == "alice"
    )
    entry = make_entry(profiles={"bob": PROFILE, "alice": PROFILE})
    assert HuaweiScaleCoordinator(hass, entry).active_profile_id == "bob"


async def test_empty_profiles_have_no_bluetooth_reader(coordinator):
    empty = HuaweiScaleCoordinator(coordinator.hass, make_entry(profiles={}))
    try:
        with patch(
            "custom_components.huawei_ah100.coordinator.bluetooth.async_register_advertisement_callback"
        ) as register:
            await empty.async_start()
        register.assert_not_called()
        assert empty.active_profile_id is None
        assert empty._task is None
        assert empty.data == {}
    finally:
        await empty.async_stop()


async def test_old_store_restores_only_the_legacy_profile_and_never_queues(sync_coordinator):
    old = {**MEASUREMENT, "received_at": "2026-01-01T08:15:00+00:00"}
    await sync_coordinator._store.async_save(old)
    await start_without_bluetooth(sync_coordinator)
    await asyncio.sleep(0)
    assert sync_coordinator.profile_data("alice") == old
    assert sync_coordinator.profile_data("bob") is None
    assert not sync_coordinator._pending_sync
    sync_coordinator._sparky.async_send.assert_not_awaited()
    stored = await sync_coordinator._store.async_load()
    assert stored == {
        "version": 2,
        "profiles": {"alice": old},
        "pending_sync": {},
        "sync_state": {},
    }


async def test_old_store_is_not_assigned_to_a_new_nonlegacy_profile(coordinator):
    old = {**MEASUREMENT, "received_at": "2026-01-01T08:15:00+00:00"}
    other = HuaweiScaleCoordinator(coordinator.hass, make_entry(profiles={"new": PROFILE}))
    await other._store.async_save(old)
    try:
        await start_without_bluetooth(other)
        assert other.profile_data("new") is None
    finally:
        await other.async_stop()


async def test_enabled_sync_persists_outbox_before_publishing(sync_coordinator):
    observed = []

    async def save(envelope):
        assert envelope["version"] == 2
        assert envelope["profiles"]["alice"]["weight_kg"] == 70.0
        assert len(envelope["pending_sync"]) == 1
        assert "synthetic-alice-key" not in str(envelope)
        observed.append("durable")

    sync_coordinator._store.async_save = save
    remove = sync_coordinator.async_add_listener(lambda: observed.append("publish"))
    try:
        await sync_coordinator._async_measurement(MEASUREMENT)
        assert observed == ["durable", "publish"]
        assert sync_coordinator.sync_status("alice") == "pending"
        sync_coordinator._sparky.async_send.assert_not_awaited()
    finally:
        remove()


async def test_outbox_same_day_coalesces_and_old_http_ack_preserves_new_reading(sync_coordinator):
    first_entered = asyncio.Event()
    second_entered = asyncio.Event()
    first_release = asyncio.Event()
    second_release = asyncio.Event()
    sent = []

    async def send(_destination, reading, _time_zone, **_kwargs):
        sent.append(reading["weight_kg"])
        if len(sent) == 1:
            first_entered.set()
            await first_release.wait()
        else:
            second_entered.set()
            await second_release.wait()

    sync_coordinator._sparky.async_send = send
    await sync_coordinator._async_measurement(MEASUREMENT)
    sync_coordinator._sync_task = asyncio.create_task(sync_coordinator._async_sync())
    try:
        await asyncio.wait_for(first_entered.wait(), 1)
        await sync_coordinator._async_measurement({**MEASUREMENT, "weight_kg": 71.0})
        first_release.set()
        await asyncio.wait_for(second_entered.wait(), 1)
        pending = sync_coordinator._pending_sync
        assert len(pending) == 1
        assert next(iter(pending.values()))["measurement"]["weight_kg"] == 71.0
        assert sync_coordinator.sync_status("alice") == "pending"
        second_release.set()
        await wait_until(lambda: sync_coordinator.sync_status("alice") == "synced")
        assert sent == [70.0, 71.0]
        stored = await sync_coordinator._store.async_load()
        assert not stored["pending_sync"]
        assert stored["profiles"]["alice"]["weight_kg"] == 71.0
    finally:
        first_release.set()
        second_release.set()


async def test_sync_metadata_write_waits_for_new_measurement_and_keeps_it(sync_coordinator):
    await sync_coordinator._async_measurement(MEASUREMENT)
    key, record = next(iter(sync_coordinator._pending_sync.items()))
    record = deepcopy(record)
    entered = asyncio.Event()
    release = asyncio.Event()
    original_save = sync_coordinator._store.async_save

    async def save(envelope):
        if envelope["profiles"]["alice"]["weight_kg"] == 71.0:
            entered.set()
            await release.wait()
        await original_save(envelope)

    sync_coordinator._store.async_save = save
    measurement = asyncio.create_task(
        sync_coordinator._async_measurement({**MEASUREMENT, "weight_kg": 71.0})
    )
    await asyncio.wait_for(entered.wait(), 1)
    ack = asyncio.create_task(sync_coordinator._async_sync_result(key, record, True))
    await asyncio.sleep(0)
    assert not ack.done()
    release.set()
    await asyncio.gather(measurement, ack)
    stored = await sync_coordinator._store.async_load()
    assert stored["profiles"]["alice"]["weight_kg"] == 71.0
    assert next(iter(stored["pending_sync"].values()))["measurement"]["weight_kg"] == 71.0


@pytest.mark.parametrize("change", ["url", "key", "disabled", "removed"])
async def test_reload_discards_old_destination_queue(sync_coordinator, change):
    await sync_coordinator._async_measurement(MEASUREMENT)
    alice = dict(sync_coordinator.profiles["alice"])
    profiles = {"alice": alice, "bob": PROFILE}
    if change == "url":
        alice["sparky_url"] = "https://new-fitness.example.test"
    elif change == "key":
        alice["sparky_api_key"] = "synthetic-new-key"
    elif change == "disabled":
        alice["sparky_enabled"] = False
    else:
        del profiles["alice"]
    entry = make_entry(profiles=profiles)
    object.__setattr__(entry, "entry_id", sync_coordinator.entry.entry_id)
    reloaded = HuaweiScaleCoordinator(sync_coordinator.hass, entry)
    reloaded._sparky.async_send = AsyncMock()
    try:
        await start_without_bluetooth(reloaded)
        await asyncio.sleep(0)
        assert not reloaded._pending_sync
        reloaded._sparky.async_send.assert_not_awaited()
        stored = await reloaded._store.async_load()
        assert not stored["pending_sync"]
        if change != "removed":
            assert reloaded.profile_data("alice")["weight_kg"] == 70.0
    finally:
        await reloaded.async_stop()


async def test_sync_failures_retry_with_backoff_and_redact_logs(
    sync_coordinator, monkeypatch, caplog
):
    monkeypatch.setattr("custom_components.huawei_ah100.coordinator.SYNC_RETRY_SECONDS", 0.01)
    synthetic_detail = "https://fitness.example.test synthetic-alice-key weight_kg=70.0"
    sync_coordinator._sparky.async_send.side_effect = [RuntimeError(synthetic_detail), None]
    await sync_coordinator._async_measurement(MEASUREMENT)
    with caplog.at_level("DEBUG", logger="custom_components.huawei_ah100.coordinator"):
        sync_coordinator._sync_task = asyncio.create_task(sync_coordinator._async_sync())
        await wait_until(lambda: sync_coordinator.sync_status("alice") == "error")
        failed = next(iter(sync_coordinator._pending_sync.values()))
        assert failed["attempts"] == 1
        assert failed["retry_at"] > 0
        await wait_until(lambda: sync_coordinator.sync_status("alice") == "synced")
    assert "RuntimeError" in caplog.text
    assert "synthetic-alice-key" not in caplog.text
    assert "fitness.example.test" not in caplog.text
    assert "weight_kg" not in caplog.text
    assert sync_coordinator._sparky.async_send.await_count == 2
    assert sync_coordinator.profile_data("alice")["weight_kg"] == 70.0


async def test_retry_backoff_is_finite_and_capped(sync_coordinator):
    await sync_coordinator._async_measurement(MEASUREMENT)
    fixed_now = datetime(2030, 1, 2, tzinfo=ZoneInfo("UTC"))
    delays = []
    with patch("custom_components.huawei_ah100.coordinator.dt_util.utcnow", return_value=fixed_now):
        for _ in range(10):
            key, record = next(iter(sync_coordinator._pending_sync.items()))
            await sync_coordinator._async_sync_result(key, deepcopy(record), False)
            queued = sync_coordinator._pending_sync[key]
            delays.append(queued["retry_at"] - fixed_now.timestamp())
    assert delays == [5, 10, 20, 40, 80, 160, 300, 300, 300, 300]
    assert sync_coordinator.sync_status("alice") == "error"


async def test_unload_cancels_http_and_leaves_durable_pending(sync_coordinator):
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def send(*_args, **_kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    sync_coordinator._sparky.async_send = send
    await sync_coordinator._async_measurement(MEASUREMENT)
    sync_coordinator._sync_task = asyncio.create_task(sync_coordinator._async_sync())
    await asyncio.wait_for(entered.wait(), 1)
    await sync_coordinator.async_stop()
    assert cancelled.is_set()
    assert len((await sync_coordinator._store.async_load())["pending_sync"]) == 1


async def test_select_noop_unknown_and_concurrent_requests(coordinator):
    updates = []
    coordinator.hass.config_entries = SimpleNamespace(
        async_update_entry=lambda entry, **kwargs: updates.append(kwargs["options"])
    )
    await coordinator.async_select_profile("alice")
    assert not updates
    assert not coordinator._paused
    with pytest.raises(ValueError):
        await coordinator.async_select_profile("unknown")
    entered = asyncio.Event()
    release = asyncio.Event()
    original_prepare = coordinator.async_prepare_reload

    async def prepare():
        entered.set()
        await release.wait()
        await original_prepare()

    coordinator.async_prepare_reload = prepare
    first = asyncio.create_task(coordinator.async_select_profile("bob"))
    await asyncio.wait_for(entered.wait(), 1)
    second = asyncio.create_task(coordinator.async_select_profile("bob"))
    release.set()
    await asyncio.gather(first, second)
    assert all(update["active_profile_id"] == "bob" for update in updates)


async def test_profile_switch_finishes_old_save_ack_and_disconnect_before_reload(
    coordinator, monkeypatch
):
    monkeypatch.setattr(session, "SLEEP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(session, "DISCONNECT_POLL_SECONDS", 0.001)
    mac = bytes.fromhex(coordinator.address.replace(":", ""))
    order = []
    client = Mock(is_connected=True)
    client.services.get_characteristic.side_effect = lambda uuid: SimpleNamespace(
        uuid=uuid, properties=("write", "notify")
    )
    subscribed = asyncio.Event()
    configured = asyncio.Event()
    acked = asyncio.Event()
    save_entered = asyncio.Event()
    save_release = asyncio.Event()
    notify_callback = None
    original_save = coordinator._store.async_save

    def emit(opcode, payload=b"", *, encrypted=False):
        frame = bytes((0xBC if encrypted else 0xBD, len(payload) + 1, opcode))
        notify_callback(None, bytearray(frame + protocol.obfuscate(payload, mac)))

    async def start_notify(_characteristic, callback):
        nonlocal notify_callback
        notify_callback = callback
        subscribed.set()

    async def write(_characteristic, frame, *, response):
        if frame[2] == 0x24:
            emit(0x26, b"\x01")
        elif frame[2] == 0x09:
            configured.set()
        elif frame[2] == 0x13:
            order.append("ack")
            acked.set()

    async def disconnect():
        order.append("disconnect")
        client.is_connected = False

    async def save(envelope):
        save_entered.set()
        await save_release.wait()
        await original_save(envelope)
        order.append("durable")

    def update(entry, *, options):
        assert not client.is_connected
        order.append("options")
        object.__setattr__(entry, "options", MappingProxyType(options))

    coordinator.hass.config_entries = SimpleNamespace(async_update_entry=update)
    coordinator._store.async_save = save
    client.start_notify = start_notify
    client.write_gatt_char = write
    client.stop_notify = AsyncMock()
    client.disconnect = disconnect
    establish = AsyncMock(return_value=client)
    with (
        patch.object(coordinator, "_get_device", return_value=Mock()),
        patch("custom_components.huawei_ah100.coordinator.establish_connection", establish),
    ):
        coordinator._task = asyncio.create_task(coordinator._async_listen())
        switch = None
        try:
            coordinator._wake.set()
            await asyncio.wait_for(subscribed.wait(), 1)
            emit(0x00)
            await asyncio.wait_for(configured.wait(), 1)
            switch = asyncio.create_task(coordinator.async_select_profile("bob"))
            await wait_until(lambda: coordinator._paused)
            assert not switch.done()
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
                protocol.aes_ctr(
                    plaintext, protocol.derive_session_key(protocol.auth_token(1), mac)
                ),
                encrypted=True,
            )
            await asyncio.wait_for(save_entered.wait(), 1)
            assert not switch.done()
            assert coordinator.profile_data("alice") is None
            assert coordinator.profile_data("bob") is None
            save_release.set()
            await asyncio.wait_for(acked.wait(), 1)
            assert not switch.done()
            emit(0x01)
            emit(0x00)
            await asyncio.wait_for(switch, 1)
            assert order == ["durable", "ack", "disconnect", "options"]
            assert coordinator.profile_data("alice")["weight_kg"] == 70.0
            assert coordinator.profile_data("bob") is None
            assert coordinator.entry.options["active_profile_id"] == "bob"
            assert establish.await_count == 1
            await asyncio.wait_for(coordinator._task, 1)
        finally:
            save_release.set()
            await coordinator.async_stop()
            if switch is not None:
                await asyncio.gather(switch, return_exceptions=True)
    replacement = HuaweiScaleCoordinator(coordinator.hass, coordinator.entry)
    replacement._store.async_load = coordinator._store.async_load
    assert replacement.active_profile_id == "bob"
    started = asyncio.Event()

    async def new_session(_client, _address, profile, _callback, **kwargs):
        assert order[-1] == "options"
        assert profile["initial_weight_kg"] == 65.0
        assert "latest_weight_kg" not in profile
        assert kwargs["user_id"] == 1
        started.set()
        await kwargs["stop_event"].wait()

    new_client = Mock(disconnect=AsyncMock())
    with (
        patch.object(replacement, "_get_device", return_value=Mock()),
        patch(
            "custom_components.huawei_ah100.coordinator.establish_connection",
            AsyncMock(return_value=new_client),
        ),
        patch("custom_components.huawei_ah100.coordinator.run_session", new_session),
    ):
        replacement._task = asyncio.create_task(replacement._async_listen())
        try:
            replacement._wake.set()
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.wait_for(replacement.async_prepare_reload(), 1)
        finally:
            await replacement.async_stop()


async def test_v2_profile_reading_survives_wire_user_id_change(coordinator):
    await coordinator._async_measurement(MEASUREMENT, "alice")
    entry = make_entry(options={"user_id": 42})
    object.__setattr__(entry, "entry_id", coordinator.entry.entry_id)
    reloaded = HuaweiScaleCoordinator(coordinator.hass, entry)
    try:
        await start_without_bluetooth(reloaded)
        assert reloaded.profile_data("alice")["weight_kg"] == 70.0
        assert reloaded.profile_data("alice")["user_id"] == 1
        assert reloaded._settings["user_id"] == 42
    finally:
        await reloaded.async_stop()


async def test_queued_day_stays_fixed_if_ha_timezone_changes(sync_coordinator):
    fixed_now = datetime(2034, 7, 11, 22, 30, tzinfo=ZoneInfo("UTC"))
    sync_coordinator.hass.config.time_zone = "Europe/Paris"
    with patch("custom_components.huawei_ah100.coordinator.dt_util.utcnow", return_value=fixed_now):
        await sync_coordinator._async_measurement(MEASUREMENT)
    record = next(iter(sync_coordinator._pending_sync.values()))
    assert record["day"] == "2034-07-12"
    sync_coordinator.hass.config.time_zone = "America/New_York"
    sync_coordinator._sync_task = asyncio.create_task(sync_coordinator._async_sync())
    await wait_until(lambda: sync_coordinator.sync_status("alice") == "synced")
    sent = sync_coordinator._sparky.async_send.call_args
    assert sent.args[2] == "America/New_York"
    assert sent.kwargs["day"] == "2034-07-12"


async def test_same_profile_is_noop_with_a_stale_active_option(coordinator):
    entry = make_entry(options={"active_profile_id": "removed"})
    stale = HuaweiScaleCoordinator(coordinator.hass, entry)
    update = Mock()
    coordinator.hass.config_entries = SimpleNamespace(async_update_entry=update)
    await stale.async_select_profile("alice")
    assert not stale._paused
    update.assert_not_called()


async def test_restart_resumes_existing_outbox_without_queuing_restored_readings(sync_coordinator):
    await sync_coordinator._async_measurement(MEASUREMENT, "alice")
    await sync_coordinator._async_measurement({**MEASUREMENT, "weight_kg": 55.0}, "bob")
    reloaded = HuaweiScaleCoordinator(sync_coordinator.hass, sync_coordinator.entry)
    reloaded._sparky.async_send = AsyncMock()
    try:
        await start_without_bluetooth(reloaded)
        await wait_until(lambda: reloaded.sync_status("alice") == "synced")
        reloaded._sparky.async_send.assert_awaited_once()
        assert reloaded._sparky.async_send.call_args.args[1]["weight_kg"] == 70.0
        assert reloaded.profile_data("alice")["weight_kg"] == 70.0
        assert reloaded.profile_data("bob")["weight_kg"] == 55.0
    finally:
        await reloaded.async_stop()


async def test_setup_with_only_initial_hint_does_not_send_health_data(sync_coordinator):
    await start_without_bluetooth(sync_coordinator)
    await asyncio.sleep(0)
    assert sync_coordinator.profile_data("alice") is None
    assert not sync_coordinator._pending_sync
    sync_coordinator._sparky.async_send.assert_not_awaited()


def capture_weighing_events(coordinator):
    events = []

    @callback
    def capture(event):
        events.append(event)

    for name in ("measurement_saved", "measurement_completed"):
        coordinator.hass.bus.async_listen(f"huawei_ah100_{name}", capture)
    return events


async def test_saved_and_disabled_completion_events_follow_durable_commit(coordinator):
    events = capture_weighing_events(coordinator)
    original_save = coordinator._store.async_save

    async def durable_save(envelope):
        assert events == []
        await original_save(envelope)

    coordinator._store.async_save = durable_save
    await coordinator._async_measurement(MEASUREMENT, "bob")
    await coordinator.hass.async_block_till_done()
    assert [event.event_type for event in events] == [
        "huawei_ah100_measurement_saved",
        "huawei_ah100_measurement_completed",
    ]
    saved, completed = [dict(event.data) for event in events]
    stored = await coordinator._store.async_load()
    assert len(saved["measurement_id"]) == 32
    assert stored["profiles"]["bob"]["measurement_id"] == saved["measurement_id"]
    assert saved == {
        "entry_id": coordinator.entry.entry_id,
        "profile_id": "bob",
        "measurement_id": saved["measurement_id"],
        "weight_kg": 70.0,
        "fat_percent": 20.0,
        "impedance_ohm": 450,
        "received_at": stored["profiles"]["bob"]["received_at"],
        "sparky_enabled": False,
    }
    assert completed == {**saved, "sync_status": "disabled"}
    assert coordinator.profile_data("alice") is None


async def test_failed_storage_emits_neither_saved_nor_completed_event(coordinator):
    events = capture_weighing_events(coordinator)
    coordinator._store.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    with pytest.raises(OSError):
        await coordinator._async_measurement(MEASUREMENT, "alice")
    await coordinator.hass.async_block_till_done()
    assert events == []
    assert coordinator.profile_data("alice") is None
    assert not coordinator._pending_sync


async def test_sync_completion_is_emitted_only_after_successful_durable_result(sync_coordinator):
    events = capture_weighing_events(sync_coordinator)
    await sync_coordinator._async_measurement(MEASUREMENT, "alice")
    await sync_coordinator.hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].event_type == "huawei_ah100_measurement_saved"
    assert events[0].data["sparky_enabled"]
    key, record = next(iter(sync_coordinator._pending_sync.items()))
    sync_coordinator._store.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    with pytest.raises(OSError):
        await sync_coordinator._async_sync_result(key, deepcopy(record), True)
    await sync_coordinator.hass.async_block_till_done()
    assert len(events) == 1
    sync_coordinator._store.async_save = AsyncMock()
    await sync_coordinator._async_sync_result(key, deepcopy(record), True)
    await sync_coordinator.hass.async_block_till_done()
    assert len(events) == 2
    assert events[1].data == {**events[0].data, "sync_status": "synced"}


async def test_http_failure_does_not_emit_completion(sync_coordinator):
    events = capture_weighing_events(sync_coordinator)
    sync_coordinator._sparky.async_send.side_effect = RuntimeError("connection unavailable")
    await sync_coordinator._async_measurement(MEASUREMENT, "alice")
    sync_coordinator._sync_task = asyncio.create_task(sync_coordinator._async_sync())
    await wait_until(lambda: sync_coordinator.sync_status("alice") == "error")
    await sync_coordinator.hass.async_block_till_done()
    assert [event.event_type for event in events] == ["huawei_ah100_measurement_saved"]
    assert sync_coordinator._pending_sync


async def test_stale_http_success_cannot_complete_a_newer_measurement(sync_coordinator):
    events = capture_weighing_events(sync_coordinator)
    await sync_coordinator._async_measurement(MEASUREMENT, "alice")
    key, old_record = next(iter(sync_coordinator._pending_sync.items()))
    old_record = deepcopy(old_record)
    await sync_coordinator._async_measurement({**MEASUREMENT, "weight_kg": 71.0}, "alice")
    await sync_coordinator._async_sync_result(key, old_record, True)
    await sync_coordinator.hass.async_block_till_done()
    assert len(events) == 2
    assert all(event.event_type == "huawei_ah100_measurement_saved" for event in events)
    current = deepcopy(sync_coordinator._pending_sync[key])
    await sync_coordinator._async_sync_result(key, current, True)
    await sync_coordinator.hass.async_block_till_done()
    assert events[-1].data["measurement_id"] == events[1].data["measurement_id"]
    assert events[-1].data["weight_kg"] == 71.0
    assert events[-1].data["sync_status"] == "synced"


@pytest.mark.parametrize("legacy_id", [None, "not-a-measurement-id"])
async def test_legacy_pending_job_without_safe_id_syncs_without_completion(
    sync_coordinator, legacy_id
):
    events = capture_weighing_events(sync_coordinator)
    await sync_coordinator._async_measurement(MEASUREMENT, "alice")
    key, record = next(iter(sync_coordinator._pending_sync.items()))
    if legacy_id is None:
        record["measurement"].pop("measurement_id")
    else:
        record["measurement"]["measurement_id"] = legacy_id
    await sync_coordinator._async_sync_result(key, deepcopy(record), True)
    await sync_coordinator.hass.async_block_till_done()
    assert len(events) == 1
    assert not sync_coordinator._pending_sync


async def test_restart_restores_readings_without_reemitting_saved_or_disabled_completion(
    coordinator,
):
    await coordinator._async_measurement(MEASUREMENT, "alice")
    await coordinator.hass.async_block_till_done()
    events = capture_weighing_events(coordinator)
    reloaded = HuaweiScaleCoordinator(coordinator.hass, coordinator.entry)
    try:
        await start_without_bluetooth(reloaded)
        await coordinator.hass.async_block_till_done()
        assert events == []
        assert reloaded.profile_data("alice")["measurement_id"]
    finally:
        await reloaded.async_stop()


async def test_confirmed_profile_changes_select_in_memory_without_options_update(coordinator):
    entry = make_entry(options={"require_profile_confirmation": True})
    confirmed = HuaweiScaleCoordinator(coordinator.hass, entry)
    options_before = dict(entry.options)
    entity = HuaweiAH100ActiveProfileSelect(confirmed)
    entity.async_write_ha_state = Mock()
    remove_listener = confirmed.async_add_listener(entity._handle_coordinator_update)
    resolved = confirmed._resolve_profile(1)
    request_id = confirmed._identity._request.request_id
    try:
        await confirmed.async_claim_weighing(request_id, "bob")
        selected = await resolved
        assert selected.profile["initial_weight_kg"] == 65.0
        assert confirmed.active_profile_id == "bob"
        assert entity.current_option == "Bob"
        entity.async_write_ha_state.assert_called_once()
        assert entry.options == options_before
        assert not confirmed._paused
        with pytest.raises(ServiceValidationError):
            await confirmed.async_claim_weighing(request_id, "alice")
    finally:
        remove_listener()
        await confirmed.async_stop()


async def test_reload_invalidates_pending_identity_and_rejects_delayed_claim(coordinator):
    entry = make_entry(options={"require_profile_confirmation": True})
    confirmed = HuaweiScaleCoordinator(coordinator.hass, entry)
    resolved = confirmed._resolve_profile(1)
    request_id = confirmed._identity._request.request_id
    try:
        await confirmed.async_prepare_reload()
        assert await resolved is None
        with pytest.raises(ServiceValidationError, match="no longer available"):
            await confirmed.async_claim_weighing(request_id, "bob")
        with pytest.raises(ValueError, match="profile is required"):
            await confirmed._async_measurement(MEASUREMENT)
    finally:
        await confirmed.async_stop()


@pytest.mark.parametrize("disk_failure", [False, True])
async def test_confirmation_ble_path_requests_only_on_wake_and_routes_only_to_claimed_owner(
    coordinator, monkeypatch, disk_failure
):
    monkeypatch.setattr(session, "SLEEP_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(session, "DISCONNECT_POLL_SECONDS", 0.001)
    entry = make_entry(options={"require_profile_confirmation": True})
    confirmed = HuaweiScaleCoordinator(coordinator.hass, entry)
    events = capture_weighing_events(confirmed)
    mac = bytes.fromhex(confirmed.address.replace(":", ""))
    client = Mock(is_connected=True)
    client.services.get_characteristic.side_effect = lambda uuid: SimpleNamespace(
        uuid=uuid, properties=("write", "notify")
    )
    subscribed = asyncio.Event()
    writes = []
    notify_callback = None
    current_time = datetime(2034, 7, 12, 9, 8, 7, tzinfo=ZoneInfo("Europe/Paris"))
    device_time = current_time + timedelta(seconds=1)

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

    client.start_notify = start_notify
    client.write_gatt_char = write
    client.stop_notify = AsyncMock()
    client.disconnect = AsyncMock()
    if disk_failure:
        confirmed._store.async_save = AsyncMock(side_effect=OSError("disk unavailable"))
    with (
        patch.object(confirmed, "_get_device", return_value=Mock()),
        patch(
            "custom_components.huawei_ah100.coordinator.establish_connection",
            AsyncMock(return_value=client),
        ),
        patch(
            "custom_components.huawei_ah100.coordinator.dt_util.now",
            side_effect=lambda: current_time,
        ),
    ):
        confirmed._task = asyncio.create_task(confirmed._async_listen())
        try:
            confirmed._wake.set()
            await asyncio.wait_for(subscribed.wait(), 1)
            assert confirmed._identity._request is None
            assert not writes
            emit(0x00)
            request_id = confirmed._identity._request.request_id
            await wait_until(lambda: any(frame[2] == 0x24 for frame in writes))
            assert not any(frame[2] == 0x09 for frame in writes)
            await confirmed.async_claim_weighing(request_id, "bob")
            await wait_until(lambda: any(frame[2] == 0x09 for frame in writes))
            current_time += timedelta(seconds=2)
            plaintext = (
                b"\x01"
                + (650).to_bytes(2, "little")
                + (200).to_bytes(2, "little")
                + device_time.year.to_bytes(2, "little")
                + bytes(
                    (
                        device_time.month,
                        device_time.day,
                        device_time.hour,
                        device_time.minute,
                        device_time.second,
                        device_time.isoweekday(),
                    )
                )
                + (450).to_bytes(2, "little")
            )
            emit(
                0x0E,
                protocol.aes_ctr(
                    plaintext, protocol.derive_session_key(protocol.auth_token(1), mac)
                ),
                encrypted=True,
            )
            if disk_failure:
                await asyncio.wait_for(confirmed._session_done.wait(), 1)
                assert not any(frame[2] == 0x13 for frame in writes)
                assert confirmed.profile_data("bob") is None
            else:
                await wait_until(lambda: any(frame[2] == 0x13 for frame in writes))
                assert confirmed.profile_data("bob")["weight_kg"] == 65.0
            assert confirmed.profile_data("alice") is None
            await confirmed.hass.async_block_till_done()
            assert len(events) == (0 if disk_failure else 2)
            assert all(event.data["profile_id"] == "bob" for event in events)
        finally:
            await confirmed.async_stop()
