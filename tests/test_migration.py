from types import MappingProxyType
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.huawei_ah100 import async_migrate_entry
from custom_components.huawei_ah100.const import (
    CONF_ACTIVE_PROFILE,
    CONF_ALLOW_BIND,
    CONF_LEGACY_PROFILE,
    CONF_USER_ID,
    DOMAIN,
    PROFILE_KEYS,
)

PROFILE = {
    "address": "AA:BB:CC:DD:EE:FF",
    "birth_date": "2000-01-31",
    "height_cm": 170,
    "sex": "female",
    "initial_weight_kg": 70.0,
    "user_id": 1,
    "allow_bind": False,
}


@pytest.fixture
async def migration_hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    with (
        patch.object(hass.config_entries, "_async_schedule_save"),
        patch.object(hass.config_entries, "_async_dispatch"),
    ):
        yield hass
    await hass.async_stop(force=True)


def make_entry(hass, *, subentries=(), version=1):
    entry = ConfigEntry(
        version=version,
        minor_version=1,
        domain=DOMAIN,
        title="Huawei AH100",
        data=PROFILE,
        options={"height_cm": 180, "user_id": 2, "allow_bind": True},
        source="user",
        unique_id=PROFILE["address"],
        discovery_keys=MappingProxyType({}),
        subentries_data=list(subentries),
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


async def test_migration_preserves_effective_profile_entry_and_sensor_history(migration_hass):
    hass = migration_hass
    entry = make_entry(hass)
    original_entry_id = entry.entry_id
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    await er.async_load(hass, load_empty=True)
    registry = er.async_get(hass)
    previous = registry.async_get_or_create(
        "sensor", DOMAIN, f"{PROFILE['address']}_weight_kg", config_entry=entry
    )

    assert await async_migrate_entry(hass, entry)

    assert entry.version == 2
    assert entry.entry_id == original_entry_id
    assert entry.data == {
        "address": PROFILE["address"],
        CONF_USER_ID: 2,
        CONF_ALLOW_BIND: True,
    }
    assert len(entry.subentries) == 1
    profile = next(iter(entry.subentries.values()))
    assert profile.subentry_type == "profile"
    assert profile.data == {
        **{key: PROFILE[key] for key in PROFILE_KEYS},
        "height_cm": 180,
        CONF_LEGACY_PROFILE: True,
    }
    assert not set(PROFILE_KEYS) & set(entry.options)
    assert entry.options[CONF_ACTIVE_PROFILE] == profile.subentry_id

    migrated = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{PROFILE['address']}_weight_kg",
        config_entry=entry,
        config_subentry_id=profile.subentry_id,
    )
    assert migrated.entity_id == previous.entity_id
    assert migrated.id == previous.id
    assert migrated.config_subentry_id == profile.subentry_id
    assert await async_migrate_entry(hass, entry)
    assert len(entry.subentries) == 1


async def test_interrupted_migration_reuses_primary_profile(migration_hass):
    entry = make_entry(
        migration_hass,
        subentries=[
            {
                "subentry_id": "existing-primary",
                "subentry_type": "profile",
                "title": "Existing profile",
                "unique_id": "primary",
                "data": {
                    **{key: PROFILE[key] for key in PROFILE_KEYS},
                    CONF_LEGACY_PROFILE: True,
                },
            }
        ],
    )
    assert await async_migrate_entry(migration_hass, entry)
    assert list(entry.subentries) == ["existing-primary"]
    assert entry.options[CONF_ACTIVE_PROFILE] == "existing-primary"


async def test_future_configuration_is_left_unchanged(migration_hass):
    entry = make_entry(migration_hass, version=3)
    assert not await async_migrate_entry(migration_hass, entry)
    assert entry.version == 3
    assert entry.data == PROFILE
    assert not entry.subentries
