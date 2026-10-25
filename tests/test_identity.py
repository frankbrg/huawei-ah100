import asyncio
from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from custom_components.huawei_ah100 import identity
from custom_components.huawei_ah100.identity import IdentityRequests

PROFILES = {
    "alice": {"name": "Alice", "person_entity_id": "person.alice"},
    "bob": {"name": "Bob", "person_entity_id": "person.bob"},
    "unmapped": {"name": "Unmapped"},
}


@pytest.fixture
async def requests(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.states.async_set("person.alice", "home", {"user_id": "alice-user"})
    hass.states.async_set("person.bob", "home", {"user_id": "bob-user"})
    events = []

    @callback
    def capture(event):
        events.append(event)

    unsubscribes = [
        hass.bus.async_listen(f"huawei_ah100_{name}", capture)
        for name in ("weighing_requested", "profile_confirmed", "weighing_cancelled")
    ]
    claimed = Mock()
    manager = IdentityRequests(hass, "scale-entry", PROFILES, claimed)
    yield manager, hass, claimed, events
    manager.cancel("stopped")
    for unsubscribe in unsubscribes:
        unsubscribe()
    await hass.async_stop(force=True)


async def test_request_event_scopes_profiles_without_demographics(requests):
    manager, hass, _, events = requests
    future = manager.request(1)
    await hass.async_block_till_done()
    event = events[0]
    assert event.event_type == "huawei_ah100_weighing_requested"
    assert event.data["entry_id"] == "scale-entry"
    assert len(event.data["request_id"]) == 32
    assert event.data["expires_at"].endswith("+00:00")
    assert event.data["profiles"] == [
        {"profile_id": profile_id, **profile} for profile_id, profile in PROFILES.items()
    ]
    manager.cancel("sleep", 1)
    assert await future is None


async def test_first_concurrent_claim_wins_without_a_second_selection(requests):
    manager, hass, claimed, events = requests
    future = manager.request(1)
    request_id = manager._request.request_id

    async def claim(profile_id):
        manager.claim(request_id, profile_id)

    results = await asyncio.gather(claim("alice"), claim("bob"), return_exceptions=True)
    assert results[0] is None
    assert isinstance(results[1], ServiceValidationError)
    assert await future == "alice"
    claimed.assert_called_once_with("alice")
    await hass.async_block_till_done()
    confirmed = [event for event in events if event.event_type.endswith("profile_confirmed")]
    assert len(confirmed) == 1
    assert confirmed[0].data == {
        "entry_id": "scale-entry",
        "request_id": request_id,
        "profile_id": "alice",
        "person_entity_id": "person.alice",
    }


@pytest.mark.parametrize("reason", ["sleep", "disconnected", "reload", "timeout", "stopped"])
async def test_cancel_invalidates_request_and_preserves_other_wake(requests, reason):
    manager, hass, claimed, events = requests
    future = manager.request(2)
    request_id = manager._request.request_id
    manager.cancel(reason, 1)
    assert not future.done()
    manager.cancel(reason, 2)
    assert await future is None
    with pytest.raises(ServiceValidationError, match="no longer available"):
        manager.claim(request_id, "alice")
    claimed.assert_not_called()
    await hass.async_block_till_done()
    assert events[-1].data == {
        "entry_id": "scale-entry",
        "request_id": request_id,
        "reason": reason,
    }


async def test_expiry_releases_waiter_and_rejects_late_claim(requests, monkeypatch):
    manager, hass, claimed, events = requests
    monkeypatch.setattr(identity, "CLAIM_TIMEOUT_SECONDS", 0.01)
    future = manager.request(1)
    request_id = manager._request.request_id
    assert await asyncio.wait_for(future, 1) is None
    with pytest.raises(ServiceValidationError, match="no longer available"):
        manager.claim(request_id, "alice")
    claimed.assert_not_called()
    await hass.async_block_till_done()
    assert events[-1].data["reason"] == "expired"


async def test_unknown_profile_and_old_request_do_not_consume_request(requests):
    manager, _, claimed, _ = requests
    future = manager.request(1)
    request_id = manager._request.request_id
    with pytest.raises(ServiceValidationError, match="profile is unavailable"):
        manager.claim(request_id, "missing")
    with pytest.raises(ServiceValidationError, match="no longer available"):
        manager.claim("old-request", "alice")
    assert not future.done()
    claimed.assert_not_called()
    manager.claim(request_id, "bob")
    assert await future == "bob"


@pytest.mark.parametrize(
    ("claimant", "context"),
    [(None, "alice-user"), ("alice-user", None), ("alice-user", "alice-user"), (None, None)],
)
async def test_claim_accepts_only_matching_authenticated_or_forwarded_user(
    requests, claimant, context
):
    manager, _, claimed, _ = requests
    future = manager.request(1)
    manager.claim(
        manager._request.request_id,
        "alice",
        claimant_user_id=claimant,
        context_user_id=context,
    )
    assert await future == "alice"
    claimed.assert_called_once_with("alice")


@pytest.mark.parametrize(
    ("profile", "claimant", "context"),
    [
        ("alice", "bob-user", None),
        ("alice", None, "bob-user"),
        ("alice", "alice-user", "bob-user"),
        ("unmapped", "alice-user", None),
        ("unmapped", None, "alice-user"),
        ("alice", "", None),
    ],
)
async def test_claim_rejects_mismatched_or_unmapped_user_without_consuming_request(
    requests, profile, claimant, context
):
    manager, _, claimed, _ = requests
    future = manager.request(1)
    with pytest.raises(ServiceValidationError, match="unavailable to this user"):
        manager.claim(
            manager._request.request_id,
            profile,
            claimant_user_id=claimant,
            context_user_id=context,
        )
    assert not future.done()
    claimed.assert_not_called()


async def test_late_claim_retry_event_identifies_only_the_claimant(requests):
    manager, hass, _, events = requests
    future = manager.request(1)
    manager.claim(manager._request.request_id, "bob")
    assert await future == "bob"
    manager.cancel("retry_required", 1)
    await hass.async_block_till_done()
    assert events[-1].data["reason"] == "retry_required"
    assert events[-1].data["profile_id"] == "bob"
    assert events[-1].data["person_entity_id"] == "person.bob"


async def test_finished_weighing_is_not_cancelled_on_sleep(requests):
    manager, hass, _, events = requests
    future = manager.request(1)
    manager.claim(manager._request.request_id, "alice")
    assert await future == "alice"
    manager.finish(1)
    manager.cancel("sleep", 1)
    await hass.async_block_till_done()
    assert not any(event.event_type.endswith("weighing_cancelled") for event in events)
