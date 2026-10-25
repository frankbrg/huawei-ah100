from datetime import UTC, datetime
from types import SimpleNamespace

from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType

from custom_components.huawei_ah100.sensor import (
    SENSOR_DESCRIPTIONS,
    SYNC_STATUS_DESCRIPTION,
    SYNC_STATUS_OPTIONS,
    HuaweiAH100Sensor,
    HuaweiAH100SyncStatusSensor,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
MEASUREMENT = {
    "weight_kg": 62.4,
    "fat_percent": 20.1,
    "impedance_ohm": 520,
    "received_at": "2026-10-25T12:00:00+00:00",
    "measured_at": "2026-10-25T14:00:00",
    "unknown_tail_hex": "a1b2",
    "user_id": 1,
    "weekday": 7,
    "raw_decrypted_hex": "private",
    "birth_date": "1990-01-01",
    "initial_weight_kg": 60.0,
    "height_cm": 180,
    "sparky_api_key": "private",
}
LEGACY_PROFILE = {
    "name": "Alex",
    "legacy_profile": True,
    "person_entity_id": "person.alex",
    "birth_date": "1990-01-01",
    "height_cm": 180,
    "sparky_api_key": "private",
}
SECOND_PROFILE = {"name": "Sam", "birth_date": "1985-03-04", "height_cm": 170}


def _coordinator(profiles, measurements=None, statuses=None, active_profile_id="legacy"):
    measurements = measurements or {}
    statuses = statuses or {}
    return SimpleNamespace(
        address=ADDRESS,
        profiles=profiles,
        active_profile_id=active_profile_id,
        profile_data=lambda profile_id: measurements.get(profile_id),
        sync_status=lambda profile_id: statuses[profile_id],
        last_update_success=False,
    )


def test_profile_sensors_keep_legacy_ids_and_only_expose_that_profile_data() -> None:
    coordinator = _coordinator(
        {"legacy": LEGACY_PROFILE, "second": SECOND_PROFILE},
        {"legacy": MEASUREMENT, "second": {**MEASUREMENT, "weight_kg": 91.2}},
    )
    entities = [
        HuaweiAH100Sensor(coordinator, "legacy", LEGACY_PROFILE, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    assert [entity.unique_id for entity in entities] == [
        f"{ADDRESS}_weight_kg",
        f"{ADDRESS}_fat_percent",
        f"{ADDRESS}_impedance_ohm",
        f"{ADDRESS}_received_at",
    ]
    assert [entity.native_value for entity in entities] == [
        62.4,
        20.1,
        520,
        datetime(2026, 10, 25, 12, tzinfo=UTC),
    ]
    assert all(entity.available for entity in entities)
    assert all(
        entity.device_info["identifiers"] == {("huawei_ah100", f"{ADDRESS}_legacy")}
        for entity in entities
    )
    assert entities[0].device_info["via_device"] == ("huawei_ah100", ADDRESS)
    assert entities[0].device_info["model"] == "AH100 profile"
    assert entities[0].device_info["entry_type"] is DeviceEntryType.SERVICE
    assert entities[0].extra_state_attributes == {
        "user_id": 1,
        "device_measured_at": "2026-10-25T14:00:00",
        "weekday": 7,
        "unknown_tail_hex": "a1b2",
        "profile_id": "legacy",
        "person_entity_id": "person.alex",
    }
    assert all(
        not {"birth_date", "height_cm", "sparky_api_key", "raw_decrypted_hex"}
        & (entity.extra_state_attributes or {}).keys()
        for entity in entities
    )
    second_weight = HuaweiAH100Sensor(
        coordinator, "second", SECOND_PROFILE, SENSOR_DESCRIPTIONS[0]
    )
    assert second_weight.native_value == 91.2
    assert second_weight.device_info["identifiers"] == {("huawei_ah100", f"{ADDRESS}_second")}
    assert second_weight.device_info["identifiers"] != entities[0].device_info["identifiers"]


def test_new_profile_ids_and_device_identity_use_stable_profile_id() -> None:
    coordinator = _coordinator({"profile-123": {"name": "Morgan"}}, {"profile-123": MEASUREMENT})
    sensors = [
        HuaweiAH100Sensor(coordinator, "profile-123", {"name": "Morgan"}, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    renamed = [
        HuaweiAH100Sensor(coordinator, "profile-123", {"name": "New Name"}, description)
        for description in SENSOR_DESCRIPTIONS
    ]
    other_profile = [
        HuaweiAH100Sensor(coordinator, "profile-456", {"name": "Morgan"}, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    assert [sensor.unique_id for sensor in sensors] == [
        f"{ADDRESS}_profile-123_{description.key}" for description in SENSOR_DESCRIPTIONS
    ]
    assert [sensor.unique_id for sensor in renamed] == [sensor.unique_id for sensor in sensors]
    assert all(
        sensor.device_info["identifiers"] == renamed[0].device_info["identifiers"]
        for sensor in renamed
    )
    assert renamed[0].device_info["identifiers"] == sensors[0].device_info["identifiers"]
    assert renamed[0].device_info["name"] == "New Name"
    assert all(
        sensor.unique_id != renamed[index].unique_id
        for index, sensor in enumerate(other_profile)
    )
    assert all(
        sensor.device_info["identifiers"] != renamed[0].device_info["identifiers"]
        for sensor in other_profile
    )


def test_sensors_without_profile_measurement_are_unavailable() -> None:
    profile = {"name": "Alex", "legacy_profile": True}
    second_profile = {"name": "Sam"}
    coordinator = _coordinator(
        {"legacy": profile, "second": second_profile}, {"second": MEASUREMENT}
    )
    entities = [
        HuaweiAH100Sensor(coordinator, "legacy", profile, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    assert all(entity.native_value is None for entity in entities)
    assert all(not entity.available for entity in entities)
    second_weight = HuaweiAH100Sensor(
        coordinator, "second", second_profile, SENSOR_DESCRIPTIONS[0]
    )
    assert second_weight.available
    assert second_weight.native_value == 62.4


def test_sync_status_is_available_without_a_measurement() -> None:
    profile = {"name": "Alex"}
    coordinator = _coordinator(
        {"profile-id": profile}, statuses={"profile-id": "disabled"}
    )
    entity = HuaweiAH100SyncStatusSensor(coordinator, "profile-id", profile)

    assert entity.unique_id == f"{ADDRESS}_profile-id_sparky_sync"
    assert entity.available
    assert entity.native_value == "disabled"
    assert entity.entity_description.device_class == "enum"
    assert entity.entity_description.options == list(SYNC_STATUS_OPTIONS)
    assert entity.entity_description.entity_category is EntityCategory.DIAGNOSTIC
    assert SYNC_STATUS_DESCRIPTION.translation_key == "sparky_sync"


async def test_setup_adds_entities_to_each_profile_subentry() -> None:
    coordinator = _coordinator({"legacy": LEGACY_PROFILE, "second": SECOND_PROFILE})
    batches = []

    def add_entities(entities, update_before_add=False, *, config_subentry_id=None):
        batches.append((list(entities), config_subentry_id))

    entry = SimpleNamespace(runtime_data=coordinator)
    await async_setup_entry(None, entry, add_entities)

    assert [subentry_id for _, subentry_id in batches] == ["legacy", "second"]
    assert [len(entities) for entities, _ in batches] == [5, 5]
    assert all(
        isinstance(entity, HuaweiAH100SyncStatusSensor)
        for entities, _ in batches
        for entity in entities[-1:]
    )
