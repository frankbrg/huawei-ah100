from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from custom_components.huawei_ah100.sparky import (
    SparkyClient,
    SparkyDestination,
    SparkySyncError,
    destination_from_profile,
    measurement_day,
    measurement_payload,
    normalize_base_url,
)

READING = {
    "weight_kg": 73.4,
    "fat_percent": 22.6,
    "impedance_ohm": 510,
    "received_at": "2034-07-11T22:30:00+00:00",
}
DESTINATION = SparkyDestination("https://fitness.example.test", "synthetic-api-key")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://fitness.example.test/", "https://fitness.example.test"),
        ("HTTP://192.168.1.42:3000/", "http://192.168.1.42:3000"),
        ("https://FITNESS.example.test", "https://fitness.example.test"),
        ("http://[::1]:3000", "http://[::1]:3000"),
    ],
)
def test_root_url_normalization(source, expected):
    assert normalize_base_url(source) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "fitness.example.test",
        "ftp://fitness.example.test",
        "https://",
        "https://user:password@fitness.example.test",
        "https://user@fitness.example.test",
        "https://fitness.example.test/api",
        "https://fitness.example.test/?secret=value",
        "https://fitness.example.test?",
        "https://fitness.example.test/#secret",
        "https://fitness.example.test#",
        "https://fitness.example.test:abc",
        "https://fitness.example.test:65536",
        "https://fitness.example.test:0",
        "https://fitness.example.test/ ",
        "https://fitness.example.test\\@other.test",
    ],
)
def test_invalid_or_nonroot_url_is_rejected_without_echoing_it(url):
    with pytest.raises(ValueError) as raised:
        normalize_base_url(url)
    assert str(raised.value) == "invalid SparkyFitness URL"


@pytest.mark.parametrize("key", [None, "", " ", "synthetic\rkey", "synthetic\nkey"])
def test_enabled_destination_requires_a_header_safe_key(key):
    with pytest.raises(ValueError):
        destination_from_profile(
            {
                "sparky_enabled": True,
                "sparky_url": DESTINATION.base_url,
                "sparky_api_key": key,
            }
        )


def test_destination_hash_changes_by_account_and_key_is_not_in_repr():
    profile = {
        "sparky_enabled": True,
        "sparky_url": DESTINATION.base_url + "/",
        "sparky_api_key": "synthetic-api-key",
    }
    destination = destination_from_profile(profile)
    assert destination == DESTINATION
    assert "synthetic-api-key" not in repr(destination)
    assert len(destination.fingerprint) == 64
    assert (
        destination.fingerprint
        != SparkyDestination(DESTINATION.base_url, "another-key").fingerprint
    )
    assert (
        destination.fingerprint
        != SparkyDestination("https://other.example.test", "synthetic-api-key").fingerprint
    )
    assert destination_from_profile({"sparky_enabled": False}) is None
    assert destination_from_profile({}) is None


@pytest.mark.parametrize(
    ("received_at", "time_zone", "day"),
    [
        ("2034-07-11T22:30:00+00:00", "Europe/Paris", "2034-07-12"),
        ("2034-07-12T00:30:00+00:00", "America/New_York", "2034-07-11"),
        ("2026-10-25T00:30:00+00:00", "Europe/Paris", "2026-10-25"),
        ("2026-10-25T01:30:00+00:00", "Europe/Paris", "2026-10-25"),
        ("2034-01-01T00:15:00+00:00", "America/Los_Angeles", "2033-12-31"),
    ],
)
def test_day_comes_from_received_timestamp_in_ha_timezone(received_at, time_zone, day):
    assert measurement_day({**READING, "received_at": received_at}, time_zone) == day


def test_payload_contains_only_supported_daily_health_fields():
    assert measurement_payload(READING, "Europe/Paris") == [
        {"type": "weight", "value": 73.4, "date": "2034-07-12"},
        {"type": "body_fat", "value": 22.6, "date": "2034-07-12"},
    ]


@pytest.mark.parametrize("fat", [None, 0, -1, 101, float("nan"), float("inf"), True, "20"])
def test_missing_or_invalid_body_fat_is_omitted(fat):
    payload = measurement_payload({**READING, "fat_percent": fat}, "UTC")
    assert payload == [{"type": "weight", "value": 73.4, "date": "2034-07-11"}]


@pytest.mark.parametrize("weight", [0, -1, 301, float("nan"), float("inf"), True, "70"])
def test_invalid_weight_is_rejected(weight):
    with pytest.raises(ValueError):
        measurement_payload({**READING, "weight_kg": weight}, "UTC")


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValueError):
        measurement_payload({**READING, "received_at": datetime(2034, 7, 12).isoformat()}, "UTC")


def success_body():
    return {
        "processed": [
            {"type": "weight", "status": "success", "data": {}},
            {"type": "body_fat", "status": "success", "data": {}},
        ],
        "errors": [],
        "skipped": [],
    }


def fake_http(*, status=200, body=None):
    response = SimpleNamespace(status=status, json=AsyncMock(return_value=body or success_body()))
    request = AsyncMock()
    request.__aenter__.return_value = response
    client = SimpleNamespace(post=Mock(return_value=request))
    return client, response


async def test_http_request_uses_exact_endpoint_array_bearer_and_finite_timeout():
    client, _ = fake_http()
    hass = Mock()
    with patch(
        "custom_components.huawei_ah100.sparky.async_get_clientsession", return_value=client
    ) as get_session:
        await SparkyClient(hass).async_send(DESTINATION, READING, "Europe/Paris")
    get_session.assert_called_once_with(hass)
    args, kwargs = client.post.call_args
    assert args == ("https://fitness.example.test/api/measurements/health-data",)
    assert kwargs["headers"] == {"Authorization": "Bearer synthetic-api-key"}
    assert kwargs["json"] == measurement_payload(READING, "Europe/Paris")
    assert kwargs["allow_redirects"] is False
    assert isinstance(kwargs["timeout"], aiohttp.ClientTimeout)
    assert kwargs["timeout"].total == 15
    assert "ssl" not in kwargs


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 500])
async def test_http_rejections_and_redirects_do_not_read_or_expose_response_body(status):
    client, response = fake_http(status=status)
    response.json.side_effect = AssertionError("must not read rejected response")
    with patch(
        "custom_components.huawei_ah100.sparky.async_get_clientsession", return_value=client
    ):
        with pytest.raises(SparkySyncError) as raised:
            await SparkyClient(Mock()).async_send(DESTINATION, READING, "UTC")
    response.json.assert_not_awaited()
    assert "synthetic-api-key" not in str(raised.value)
    assert "fitness.example.test" not in str(raised.value)


@pytest.mark.parametrize(
    "body",
    [
        {"processed": [], "errors": [], "skipped": []},
        {
            "processed": [],
            "errors": [{"error": "secret synthetic body", "entry": READING}],
            "skipped": [],
        },
        {"processed": [], "errors": [], "skipped": [{"entry": READING}]},
        {"processed": [{"type": "weight", "status": "success"}], "errors": [], "skipped": []},
        {
            "processed": [
                {"type": "weight", "status": "success"},
                {"type": None, "status": "success"},
            ],
            "errors": [],
            "skipped": [],
        },
        {
            "processed": [
                {"type": "weight", "status": "success"},
                {"type": "body_fat", "status": "error"},
            ],
            "errors": [],
            "skipped": [],
        },
        {"message": "okay"},
        [],
    ],
)
async def test_http_200_requires_per_record_confirmation_and_never_echoes_body(body):
    client, response = fake_http()
    response.json.return_value = body
    with patch(
        "custom_components.huawei_ah100.sparky.async_get_clientsession", return_value=client
    ):
        with pytest.raises(SparkySyncError) as raised:
            await SparkyClient(Mock()).async_send(DESTINATION, READING, "UTC")
    assert "secret synthetic body" not in str(raised.value)
    assert "weight_kg" not in str(raised.value)


async def test_invalid_json_is_a_generic_sync_failure():
    client, response = fake_http()
    response.json.side_effect = ValueError("synthetic response with health data")
    with patch(
        "custom_components.huawei_ah100.sparky.async_get_clientsession", return_value=client
    ):
        with pytest.raises(SparkySyncError) as raised:
            await SparkyClient(Mock()).async_send(DESTINATION, READING, "UTC")
    assert str(raised.value) == "invalid SparkyFitness response"


async def test_http_uses_frozen_queue_day_even_after_timezone_changes():
    client, _ = fake_http()
    with patch(
        "custom_components.huawei_ah100.sparky.async_get_clientsession", return_value=client
    ):
        await SparkyClient(Mock()).async_send(
            DESTINATION, READING, "America/New_York", day="2034-07-12"
        )
    assert all(item["date"] == "2034-07-12" for item in client.post.call_args.kwargs["json"])


@pytest.mark.parametrize("day", ["2034-07-99", "20340712", "2034-7-12", "2034-07-12T00:00:00", 123])
def test_invalid_frozen_day_is_rejected(day):
    with pytest.raises(ValueError):
        measurement_payload(READING, "UTC", day=day)
