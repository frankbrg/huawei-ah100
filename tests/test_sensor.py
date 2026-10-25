from datetime import UTC, datetime
from types import SimpleNamespace

from custom_components.huawei_ah100.sensor import (
    SENSOR_DESCRIPTIONS,
    HuaweiAH100Sensor,
)


def test_sensor_entities_expose_only_live_measurement_and_allowed_attributes() -> None:
    coordinator = SimpleNamespace(
        address="AA:BB:CC:DD:EE:FF",
        data={
            "weight_kg": 62.4,
            "fat_percent": None,
            "impedance_ohm": 520,
            "received_at": "2026-10-25T12:00:00+00:00",
            "measured_at": "2026-10-25T14:00:00",
            "unknown_tail_hex": "a1b2",
            "user_id": 1,
            "weekday": 7,
            "raw_decrypted_hex": "private",
            "birth_date": "1990-01-01",
            "initial_weight_kg": 60.0,
        },
        last_update_success=False,
    )
    entities = [HuaweiAH100Sensor(coordinator, description) for description in SENSOR_DESCRIPTIONS]

    assert [entity.unique_id for entity in entities] == [
        "AA:BB:CC:DD:EE:FF_weight_kg",
        "AA:BB:CC:DD:EE:FF_fat_percent",
        "AA:BB:CC:DD:EE:FF_impedance_ohm",
        "AA:BB:CC:DD:EE:FF_received_at",
    ]
    assert [entity.native_value for entity in entities] == [
        62.4,
        None,
        520,
        datetime(2026, 10, 25, 12, tzinfo=UTC),
    ]
    assert all(entity.available for entity in entities)
    assert all(
        entity.device_info["identifiers"] == {("huawei_ah100", "AA:BB:CC:DD:EE:FF")}
        for entity in entities
    )
    assert entities[0].extra_state_attributes == {
        "user_id": 1,
        "device_measured_at": "2026-10-25T14:00:00",
        "weekday": 7,
        "unknown_tail_hex": "a1b2",
    }
    assert all(
        "raw_decrypted_hex" not in (entity.extra_state_attributes or {}) for entity in entities
    )


def test_entities_without_a_measurement_have_no_value_and_are_unavailable() -> None:
    coordinator = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", data=None)
    entities = [HuaweiAH100Sensor(coordinator, description) for description in SENSOR_DESCRIPTIONS]

    assert all(entity.native_value is None for entity in entities)
    assert all(not entity.available for entity in entities)
