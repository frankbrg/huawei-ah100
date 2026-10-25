from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.huawei_ah100.select import (
    HuaweiAH100ActiveProfileSelect,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:FF"
PROFILES = {
    "profile-1": {"name": "Alex"},
    "profile-2": {"name": "Sam"},
}


def _coordinator(profiles=PROFILES, active_profile_id="profile-2"):
    return SimpleNamespace(
        address=ADDRESS,
        profiles=profiles,
        active_profile_id=active_profile_id,
        async_select_profile=AsyncMock(),
    )


def test_active_profile_select_shows_names_and_physical_scale_device() -> None:
    entity = HuaweiAH100ActiveProfileSelect(_coordinator())

    assert entity.unique_id == f"{ADDRESS}_active_profile"
    assert entity.entity_description.translation_key == "active_profile"
    assert entity.available
    assert entity.options == ["Alex", "Sam"]
    assert entity.current_option == "Sam"
    assert entity.device_info["identifiers"] == {("huawei_ah100", ADDRESS)}
    assert entity.device_info["model"] == "AH100"


async def test_select_option_resolves_the_profile_id() -> None:
    coordinator = _coordinator()
    entity = HuaweiAH100ActiveProfileSelect(coordinator)

    await entity.async_select_option("Alex")

    coordinator.async_select_profile.assert_awaited_once_with("profile-1")


async def test_select_option_rejects_unknown_profile_name() -> None:
    coordinator = _coordinator()
    entity = HuaweiAH100ActiveProfileSelect(coordinator)

    with pytest.raises(ValueError, match="Unknown profile option"):
        await entity.async_select_option("Unknown")

    coordinator.async_select_profile.assert_not_awaited()


def test_select_is_unavailable_when_there_are_no_profiles() -> None:
    entity = HuaweiAH100ActiveProfileSelect(_coordinator(profiles={}, active_profile_id=None))

    assert not entity.available
    assert entity.options == []
    assert entity.current_option is None


async def test_setup_adds_one_select_to_the_physical_scale() -> None:
    batches = []

    def add_entities(entities, update_before_add=False, *, config_subentry_id=None):
        batches.append((list(entities), config_subentry_id))

    entry = SimpleNamespace(runtime_data=_coordinator())
    await async_setup_entry(None, entry, add_entities)

    assert len(batches) == 1
    entities, config_subentry_id = batches[0]
    assert len(entities) == 1
    assert isinstance(entities[0], HuaweiAH100ActiveProfileSelect)
    assert config_subentry_id is None
