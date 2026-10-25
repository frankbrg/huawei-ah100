from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import AbortFlow

from custom_components.huawei_ah100 import config_flow
from custom_components.huawei_ah100.config_flow import (
    OPTIONS_SCHEMA,
    USER_SCHEMA,
    HuaweiAH100ConfigFlow,
    HuaweiAH100OptionsFlow,
    _normalize_address,
    _profile_data,
    _profile_errors,
)
from custom_components.huawei_ah100.const import (
    CONF_ALLOW_BIND,
    CONF_BIRTH_DATE,
    CONF_HEIGHT,
    CONF_INITIAL_WEIGHT,
    CONF_SEX,
    CONF_USER_ID,
    DOMAIN,
)


def _profile_input(**overrides: object) -> dict[str, object]:
    return {
        CONF_BIRTH_DATE: "2000-01-02",
        CONF_HEIGHT: 170.0,
        CONF_SEX: "female",
        CONF_INITIAL_WEIGHT: 62.4,
        CONF_USER_ID: 1.0,
        CONF_ALLOW_BIND: False,
        **overrides,
    }


def _configured_flow(existing_entry: object | None = None) -> HuaweiAH100ConfigFlow:
    flow = HuaweiAH100ConfigFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            flow=SimpleNamespace(
                async_progress_by_handler=Mock(return_value=[]), async_abort=Mock()
            ),
            async_entries=Mock(return_value=[]),
            async_entry_for_domain_unique_id=Mock(return_value=existing_entry),
        )
    )
    flow.handler = DOMAIN
    flow.flow_id = "test-flow"
    flow.context = {"source": "user"}
    return flow


def _address_selector(result: dict[str, object]) -> object:
    schema = result["data_schema"]
    return next(
        value for key, value in schema.schema.items() if getattr(key, "schema", key) == CONF_ADDRESS
    )


def _discovery(
    address: str, name: str = "CH100 scale", connectable: bool = True
) -> SimpleNamespace:
    return SimpleNamespace(address=address, name=name, connectable=connectable)


def test_normalize_bluetooth_address() -> None:
    assert _normalize_address("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_address("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_address("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_address("AA:BB:CC:DD:EE:FG") is None
    assert _normalize_address("AA:BB:CC:DD:EE") is None


def test_user_schema_defaults_and_stores_canonical_profile() -> None:
    user_input = USER_SCHEMA(
        {
            CONF_ADDRESS: "aa:bb:cc:dd:ee:ff",
            CONF_BIRTH_DATE: "2000-01-02",
            CONF_HEIGHT: 170,
            CONF_SEX: "female",
            CONF_INITIAL_WEIGHT: 62.4,
        }
    )

    assert user_input[CONF_USER_ID] == 1.0
    assert user_input[CONF_ALLOW_BIND] is False
    assert _profile_errors(user_input, date(2026, 10, 25)) == {}
    assert _profile_data(user_input) == {
        CONF_BIRTH_DATE: "2000-01-02",
        CONF_HEIGHT: 170,
        CONF_SEX: "female",
        CONF_INITIAL_WEIGHT: 62.4,
        CONF_USER_ID: 1,
        CONF_ALLOW_BIND: False,
    }


@pytest.mark.parametrize(
    ("birth_date", "error"),
    [
        (date(2026, 10, 26), "future_birth_date"),
        (date(1898, 10, 25), "age_out_of_range"),
    ],
)
def test_birth_date_bounds(birth_date: date, error: str) -> None:
    errors = _profile_errors(
        {
            CONF_BIRTH_DATE: birth_date,
            CONF_HEIGHT: 170.0,
            CONF_SEX: "male",
            CONF_INITIAL_WEIGHT: 60.0,
            CONF_USER_ID: 1.0,
            CONF_ALLOW_BIND: False,
        },
        date(2026, 10, 25),
    )

    assert errors == {CONF_BIRTH_DATE: error}


@pytest.mark.parametrize("birth_date", [date(2026, 10, 25), date(1899, 10, 25)])
def test_age_zero_and_127_are_allowed(birth_date: date) -> None:
    assert (
        _profile_errors(
            {
                CONF_BIRTH_DATE: birth_date,
                CONF_HEIGHT: 1.0,
                CONF_SEX: "female",
                CONF_INITIAL_WEIGHT: 300.0,
                CONF_USER_ID: 255.0,
                CONF_ALLOW_BIND: True,
            },
            date(2026, 10, 25),
        )
        == {}
    )


def test_profile_rejects_fractional_dimensions_invalid_weight_and_user_id() -> None:
    errors = _profile_errors(
        {
            CONF_BIRTH_DATE: date(2000, 1, 1),
            CONF_HEIGHT: 170.5,
            CONF_SEX: "male",
            CONF_INITIAL_WEIGHT: 0.0,
            CONF_USER_ID: 1.5,
            CONF_ALLOW_BIND: False,
        },
        date(2026, 10, 25),
    )

    assert errors == {
        CONF_HEIGHT: "height_not_integer",
        CONF_INITIAL_WEIGHT: "invalid_weight",
        CONF_USER_ID: "user_id_not_integer",
    }


@pytest.mark.parametrize("weight", [float("nan"), float("inf"), float("-inf")])
def test_profile_rejects_non_finite_weight(weight: float) -> None:
    errors = _profile_errors(
        {
            CONF_BIRTH_DATE: date(2000, 1, 1),
            CONF_HEIGHT: 170.0,
            CONF_SEX: "male",
            CONF_INITIAL_WEIGHT: weight,
            CONF_USER_ID: 1.0,
            CONF_ALLOW_BIND: False,
        },
        date(2026, 10, 25),
    )

    assert errors == {CONF_INITIAL_WEIGHT: "invalid_weight"}


@pytest.mark.parametrize("connectable,name", [(False, "CH100 scale"), (True, "other device")])
async def test_bluetooth_discovery_rejects_unsupported_devices(
    connectable: bool, name: str
) -> None:
    flow = HuaweiAH100ConfigFlow()

    result = await flow.async_step_bluetooth(
        SimpleNamespace(connectable=connectable, name=name, address="AA:BB:CC:DD:EE:FF")
    )

    assert result["type"] == "abort"
    assert result["reason"] == "unsupported_device"


async def test_config_flow_aborts_when_mac_is_already_configured() -> None:
    flow = _configured_flow(SimpleNamespace(source="user"))

    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_manual({CONF_ADDRESS: "aa:bb:cc:dd:ee:ff", **_profile_input()})


async def test_user_step_offers_normalized_deduplicated_nearby_scales(monkeypatch) -> None:
    flow = _configured_flow()
    existing = SimpleNamespace(unique_id="11:22:33:44:55:66")
    flow.hass.config_entries.async_entries.return_value = [existing]
    request_active_scan = AsyncMock()
    discovered_service_info = Mock(
        return_value=[
            _discovery("aa:bb:cc:dd:ee:ff", "CH100 scale   "),
            _discovery("AA-BB-CC-DD-EE-FF", "CH100 duplicate"),
            _discovery("11:22:33:44:55:66"),
            _discovery("AA:BB:CC:DD:EE:01", connectable=False),
            _discovery("AA:BB:CC:DD:EE:02", name="Other device"),
            _discovery("not-a-mac"),
            _discovery("AA:BB:CC:DD:EE:03", "CH100 second scale"),
        ]
    )
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", request_active_scan)
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered_service_info
    )

    result = await flow.async_step_user()

    assert result["type"] == "form"
    assert result["step_id"] == "select_device"
    request_active_scan.assert_awaited_once_with(flow.hass)
    discovered_service_info.assert_called_once_with(flow.hass, connectable=True)
    selector = _address_selector(result)
    options = selector.config["options"]
    assert [option["value"] for option in options] == [
        "AA:BB:CC:DD:EE:FF",
        "AA:BB:CC:DD:EE:03",
        "__manual__",
    ]
    assert options[0]["label"] == "CH100 scale (AA:BB:CC:DD:EE:FF)"
    assert options[-1]["label"] == "Manual setup"
    assert selector.config["translation_key"] == "nearby_scale"


async def test_nearby_selection_shows_profile_only_then_creates_entry(monkeypatch) -> None:
    flow = _configured_flow()
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", AsyncMock())
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=[_discovery("aa:bb:cc:dd:ee:ff")]),
    )

    await flow.async_step_user()
    profile_form = await flow.async_step_select_device({CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"})
    result = await flow.async_step_bluetooth(_profile_input())

    assert profile_form["type"] == "form"
    assert profile_form["step_id"] == "bluetooth"
    assert all(key.schema != CONF_ADDRESS for key in profile_form["data_schema"].schema)
    assert result["type"] == "create_entry"
    assert result["data"][CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"


async def test_user_setup_finishes_while_auto_bluetooth_flow_is_pending(monkeypatch) -> None:
    flow = _configured_flow()
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", AsyncMock())
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=[_discovery("AA:BB:CC:DD:EE:FF")]),
    )
    await flow.async_step_user()
    flow.hass.config_entries.flow.async_progress_by_handler.return_value = [
        {
            "flow_id": "bluetooth-flow",
            "context": {
                "source": "bluetooth",
                "unique_id": "AA:BB:CC:DD:EE:FF",
            },
        }
    ]

    profile_form = await flow.async_step_select_device({CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"})
    result = await flow.async_step_bluetooth(_profile_input())

    assert profile_form["step_id"] == "bluetooth"
    assert result["type"] == "create_entry"
    assert result["data"][CONF_ADDRESS] == "AA:BB:CC:DD:EE:FF"


async def test_no_discoveries_offers_refresh_and_manual_fallback(monkeypatch) -> None:
    flow = _configured_flow()
    request_active_scan = AsyncMock()
    discovered_service_info = Mock(side_effect=[[], [_discovery("AA:BB:CC:DD:EE:FF")]])
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", request_active_scan)
    monkeypatch.setattr(
        config_flow.bluetooth, "async_discovered_service_info", discovered_service_info
    )

    menu = await flow.async_step_user()
    manual_form = await flow.async_step_manual()
    refreshed = await flow.async_step_scan_again()

    assert menu["type"] == "menu"
    assert menu["step_id"] == "user"
    assert menu["menu_options"] == ["scan_again", "manual"]
    assert manual_form["type"] == "form"
    assert manual_form["step_id"] == "manual"
    assert CONF_ADDRESS in [key.schema for key in manual_form["data_schema"].schema]
    assert refreshed["type"] == "form"
    assert refreshed["step_id"] == "select_device"
    assert request_active_scan.await_count == 2
    assert discovered_service_info.call_count == 2


async def test_manual_sentinel_in_device_selector_opens_manual_form(monkeypatch) -> None:
    flow = _configured_flow()
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", AsyncMock())
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=[_discovery("AA:BB:CC:DD:EE:FF")]),
    )

    await flow.async_step_user()
    result = await flow.async_step_select_device({CONF_ADDRESS: "__manual__"})

    assert result["type"] == "form"
    assert result["step_id"] == "manual"


async def test_selected_device_duplicate_race_is_aborted(monkeypatch) -> None:
    flow = _configured_flow()
    monkeypatch.setattr(config_flow.bluetooth, "async_request_active_scan", AsyncMock())
    monkeypatch.setattr(
        config_flow.bluetooth,
        "async_discovered_service_info",
        Mock(return_value=[_discovery("AA:BB:CC:DD:EE:FF")]),
    )

    await flow.async_step_user()
    flow.hass.config_entries.async_entry_for_domain_unique_id.return_value = SimpleNamespace(
        source="user"
    )
    with pytest.raises(AbortFlow, match="already_configured"):
        await flow.async_step_select_device({CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"})


async def test_connectable_ch100_discovery_creates_entry_with_normalized_address() -> None:
    flow = _configured_flow()
    flow.context = {"source": "bluetooth"}
    discovered = SimpleNamespace(
        connectable=True,
        name="CH100 scale",
        address="aa-bb-cc-dd-ee-ff",
    )

    form = await flow.async_step_bluetooth(discovered)
    result = await flow.async_step_bluetooth(_profile_input())

    assert form["type"] == "form"
    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
        CONF_BIRTH_DATE: "2000-01-02",
        CONF_HEIGHT: 170,
        CONF_SEX: "female",
        CONF_INITIAL_WEIGHT: 62.4,
        CONF_USER_ID: 1,
        CONF_ALLOW_BIND: False,
    }


async def test_options_flow_changes_profile_without_exposing_mac() -> None:
    options_flow = HuaweiAH100OptionsFlow()
    input_with_address = _profile_input(address="AA:BB:CC:DD:EE:FF", user_id=2.0, allow_bind=True)

    result = await options_flow.async_step_init(input_with_address)

    assert all(key.schema != CONF_ADDRESS for key in OPTIONS_SCHEMA.schema)
    assert result["type"] == "create_entry"
    assert CONF_ADDRESS not in result["data"]
    assert result["data"][CONF_USER_ID] == 2
    assert result["data"][CONF_ALLOW_BIND] is True
