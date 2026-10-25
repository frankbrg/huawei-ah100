import asyncio
from types import MappingProxyType
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

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
