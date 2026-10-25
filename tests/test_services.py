from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import probatio
import pytest
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from custom_components.huawei_ah100.services import (
    CLAIM_WEIGHING_SCHEMA,
    async_setup_services,
)


@pytest.fixture
async def service_hass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coordinator = SimpleNamespace(async_claim_weighing=AsyncMock())
    entry = SimpleNamespace(domain="huawei_ah100", runtime_data=coordinator)
    hass.config_entries = SimpleNamespace(async_get_entry=Mock(return_value=entry))
    async_setup_services(hass)
    yield hass, coordinator
    await hass.async_stop(force=True)


async def test_claim_service_passes_authenticated_and_original_claimant_context(service_hass):
    hass, coordinator = service_hass
    async_setup_services(hass)
    await hass.services.async_call(
        "huawei_ah100",
        "claim_weighing",
        {
            "entry_id": "scale-entry",
            "request_id": "request",
            "profile_id": "alice",
            "claimant_user_id": "alice-user",
        },
        blocking=True,
        context=Context(user_id="alice-user"),
    )
    hass.config_entries.async_get_entry.assert_called_once_with("scale-entry")
    coordinator.async_claim_weighing.assert_awaited_once_with(
        "request", "alice", claimant_user_id="alice-user", context_user_id="alice-user"
    )


async def test_automatic_claim_service_allows_absent_user_context(service_hass):
    hass, coordinator = service_hass
    await hass.services.async_call(
        "huawei_ah100",
        "claim_weighing",
        {"entry_id": "scale-entry", "request_id": "request", "profile_id": "alice"},
        blocking=True,
    )
    coordinator.async_claim_weighing.assert_awaited_once_with(
        "request", "alice", claimant_user_id=None, context_user_id=None
    )


@pytest.mark.parametrize(
    "entry", [None, SimpleNamespace(domain="other"), SimpleNamespace(domain="huawei_ah100")]
)
async def test_claim_service_rejects_missing_wrong_or_unloaded_entry(service_hass, entry):
    hass, coordinator = service_hass
    hass.config_entries.async_get_entry.return_value = entry
    with pytest.raises(ServiceValidationError, match="scale is unavailable"):
        await hass.services.async_call(
            "huawei_ah100",
            "claim_weighing",
            {"entry_id": "missing", "request_id": "request", "profile_id": "alice"},
            blocking=True,
        )
    coordinator.async_claim_weighing.assert_not_awaited()


@pytest.mark.parametrize("missing", ["entry_id", "request_id", "profile_id"])
def test_claim_service_schema_requires_explicit_request_and_profile(missing):
    data = {"entry_id": "scale-entry", "request_id": "request", "profile_id": "alice"}
    data.pop(missing)
    with pytest.raises(probatio.Invalid):
        CLAIM_WEIGHING_SCHEMA(data)
