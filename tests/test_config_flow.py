from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.data_entry_flow import AbortFlow

from custom_components.huawei_ah100 import config_flow
from custom_components.huawei_ah100.config_flow import (
    OPTIONS_SCHEMA,
    PROFILE_SCHEMA,
    USER_SCHEMA,
    HuaweiAH100ConfigFlow,
    HuaweiAH100OptionsFlow,
    HuaweiAH100ProfileFlow,
    _connection_data,
    _connection_errors,
    _normalize_address,
    _profile_data,
    _profile_errors,
)
from custom_components.huawei_ah100.const import (
    CONF_ACTIVE_PROFILE,
    CONF_ALLOW_BIND,
    CONF_BIRTH_DATE,
    CONF_HEIGHT,
    CONF_INITIAL_WEIGHT,
    CONF_LEGACY_PROFILE,
    CONF_PERSON,
    CONF_SEX,
    CONF_SPARKY_API_KEY,
    CONF_SPARKY_ENABLED,
    CONF_SPARKY_URL,
    CONF_USER_ID,
    DOMAIN,
    SUBENTRY_PROFILE,
)


def _profile_input(**overrides: object) -> dict[str, object]:
    return {
        CONF_NAME: "Profile 1",
        CONF_BIRTH_DATE: "2000-01-02",
        CONF_HEIGHT: 170.0,
        CONF_SEX: "female",
        CONF_INITIAL_WEIGHT: 62.4,
        CONF_USER_ID: 1.0,
        CONF_ALLOW_BIND: False,
        CONF_SPARKY_ENABLED: False,
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


def _profile_entry(subentries: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    subentries = subentries or []
    return SimpleNamespace(
        entry_id="entry-id",
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF", CONF_USER_ID: 1, CONF_ALLOW_BIND: False},
        options={},
        subentries={subentry.subentry_id: subentry for subentry in subentries},
        get_subentries_of_type=Mock(
            side_effect=lambda subentry_type: [
                subentry for subentry in subentries if subentry.subentry_type == subentry_type
            ]
        ),
    )


def _profile_subentry(
    *,
    subentry_id: str = "profile-id",
    title: str = "Primary",
    data: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        subentry_id=subentry_id,
        subentry_type=SUBENTRY_PROFILE,
        title=title,
        data=data or _profile_data(_profile_input(), legacy_profile=True),
    )


def _profile_flow(
    entry: SimpleNamespace, *, reconfigure_subentry_id: str | None = None
) -> HuaweiAH100ProfileFlow:
    flow = HuaweiAH100ProfileFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_known_entry=Mock(return_value=entry),
            async_update_subentry=Mock(return_value=True),
        )
    )
    flow.handler = (entry.entry_id, SUBENTRY_PROFILE)
    flow.flow_id = "profile-flow"
    flow.context = (
        {"source": "reconfigure", "subentry_id": reconfigure_subentry_id}
        if reconfigure_subentry_id is not None
        else {"source": "user"}
    )
    return flow


def _options_flow(options: dict[str, object] | None = None) -> HuaweiAH100OptionsFlow:
    entry = SimpleNamespace(
        entry_id="entry-id",
        domain=DOMAIN,
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"},
        options=options or {},
    )
    flow = HuaweiAH100OptionsFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_known_entry=Mock(return_value=entry))
    )
    flow.handler = entry.entry_id
    flow.flow_id = "options-flow"
    flow.context = {"source": "user"}
    return flow


def test_normalize_bluetooth_address() -> None:
    assert _normalize_address("aa:bb:cc:dd:ee:ff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_address("aabbccddeeff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_address("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert _normalize_address("AA:BB:CC:DD:EE:FG") is None
    assert _normalize_address("AA:BB:CC:DD:EE") is None


def test_initial_schema_separates_connection_and_profile_data() -> None:
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
    assert user_input[CONF_NAME] == "Profile 1"
    assert user_input[CONF_SPARKY_ENABLED] is False
    assert USER_SCHEMA.schema[CONF_BIRTH_DATE].config["type"] == "text"
    assert _profile_errors(user_input, date(2026, 10, 25)) == {}
    assert _connection_data(user_input) == {
        CONF_USER_ID: 1,
        CONF_ALLOW_BIND: False,
    }
    assert _profile_data(user_input, legacy_profile=True) == {
        CONF_BIRTH_DATE: "2000-01-02",
        CONF_HEIGHT: 170,
        CONF_SEX: "female",
        CONF_INITIAL_WEIGHT: 62.4,
        CONF_SPARKY_ENABLED: False,
        CONF_LEGACY_PROFILE: True,
    }
    person_selector = next(
        value for key, value in PROFILE_SCHEMA.schema.items() if key.schema == CONF_PERSON
    )
    assert person_selector.config["domain"] == ["person"]
    assert CONF_PERSON in [key.schema for key in USER_SCHEMA.schema]
    assert CONF_PERSON in [key.schema for key in PROFILE_SCHEMA.schema]


@pytest.mark.parametrize(
    ("birth_date", "canonical"),
    [
        ("2000-01-02", "2000-01-02"),
        ("02/01/2000", "2000-01-02"),
        ("01/02/2000", "2000-02-01"),
        ("29/02/2024", "2024-02-29"),
    ],
)
def test_birth_date_formats_store_iso(birth_date: str, canonical: str) -> None:
    user_input = USER_SCHEMA(_profile_input(address="AA:BB:CC:DD:EE:FF", birth_date=birth_date))

    assert _profile_errors(user_input, date(2026, 10, 25)) == {}
    assert _profile_data(user_input)[CONF_BIRTH_DATE] == canonical


@pytest.mark.parametrize(
    "birth_date",
    [
        "29/02/2023",
        "2023-02-29",
        "1/02/2000",
        "01-02-2000",
        "01/02/03",
        "2000-2-01",
        " 2000-01-02",
    ],
)
def test_birth_date_rejects_invalid_or_malformed_values(birth_date: str) -> None:
    assert _profile_errors(
        {**_profile_input(), CONF_BIRTH_DATE: birth_date}, date(2026, 10, 25)
    ) == {CONF_BIRTH_DATE: "invalid_birth_date"}


def test_french_birth_date_still_rejects_future_dates() -> None:
    assert _profile_errors(
        {**_profile_input(), CONF_BIRTH_DATE: "26/10/2026"}, date(2026, 10, 25)
    ) == {CONF_BIRTH_DATE: "future_birth_date"}


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
    }
    assert _connection_errors({CONF_USER_ID: 1.5, CONF_ALLOW_BIND: False}) == {
        CONF_USER_ID: "user_id_not_integer"
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
        "manual",
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
    assert result["data"] == {
        CONF_ADDRESS: "AA:BB:CC:DD:EE:FF",
        CONF_USER_ID: 1,
        CONF_ALLOW_BIND: False,
    }
    assert result["subentries"] == [
        {
            "subentry_type": SUBENTRY_PROFILE,
            "data": _profile_data(_profile_input(), legacy_profile=True),
            "title": "Profile 1",
            "unique_id": "primary",
        }
    ]


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
    assert result["subentries"][0]["unique_id"] == "primary"


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
    result = await flow.async_step_select_device({CONF_ADDRESS: "manual"})

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
        CONF_USER_ID: 1,
        CONF_ALLOW_BIND: False,
    }
    assert result["subentries"][0] == {
        "subentry_type": SUBENTRY_PROFILE,
        "data": _profile_data(_profile_input(), legacy_profile=True),
        "title": "Profile 1",
        "unique_id": "primary",
    }


async def test_options_flow_only_changes_shared_connection_settings() -> None:
    options_flow = _options_flow()
    input_with_profile_data = _profile_input(user_id=2.0, allow_bind=True)

    result = await options_flow.async_step_init(input_with_profile_data)

    assert all(key.schema != CONF_ADDRESS for key in OPTIONS_SCHEMA.schema)
    assert [key.schema for key in OPTIONS_SCHEMA.schema] == [CONF_USER_ID, CONF_ALLOW_BIND]
    assert result["type"] == "create_entry"
    assert CONF_ADDRESS not in result["data"]
    assert result["data"] == {CONF_USER_ID: 2, CONF_ALLOW_BIND: True}


async def test_options_flow_preserves_active_profile_and_other_options() -> None:
    options_flow = _options_flow(
        {CONF_ACTIVE_PROFILE: "profile-b", "unrelated_option": "preserved"}
    )

    result = await options_flow.async_step_init({CONF_USER_ID: 2.0, CONF_ALLOW_BIND: True})

    assert result["type"] == "create_entry"
    assert result["data"] == {
        CONF_ACTIVE_PROFILE: "profile-b",
        "unrelated_option": "preserved",
        CONF_USER_ID: 2,
        CONF_ALLOW_BIND: True,
    }


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://sparky.example",
        "https://sparky.example/health",
        "https://user:secret@sparky.example",
        "https://sparky.example?mode=1",
        "https://sparky.example#section",
        "https://sparky.example\\path",
        "https://sparky.example:70000",
    ],
)
def test_sparky_enabled_rejects_invalid_base_urls(url: str) -> None:
    errors = _profile_errors(
        _profile_input(
            sparky_enabled=True,
            sparky_url=url,
            sparky_api_key="secret",
        ),
        date(2026, 10, 25),
    )

    assert errors == {CONF_SPARKY_URL: "invalid_sparky_url"}


def test_sparky_enabled_requires_single_line_api_key() -> None:
    missing_key = _profile_errors(
        _profile_input(sparky_enabled=True, sparky_url="https://sparky.example"),
        date(2026, 10, 25),
    )
    multiline_key = _profile_errors(
        _profile_input(
            sparky_enabled=True,
            sparky_url="https://sparky.example",
            sparky_api_key="secret\nforged",
        ),
        date(2026, 10, 25),
    )

    assert missing_key == {CONF_SPARKY_API_KEY: "missing_sparky_api_key"}
    assert multiline_key == {CONF_SPARKY_API_KEY: "invalid_sparky_api_key"}


def test_sparky_disabled_skips_sync_validation_and_enabled_url_is_normalized() -> None:
    disabled = _profile_input(sparky_enabled=False, sparky_url="not a URL")
    enabled = _profile_input(
        sparky_enabled=True,
        sparky_url="HTTPS://Sparky.Example:8443/",
        sparky_api_key=" secret ",
    )

    assert _profile_errors(disabled, date(2026, 10, 25)) == {}
    assert _profile_errors(enabled, date(2026, 10, 25)) == {}
    assert _profile_data(enabled)[CONF_SPARKY_URL] == "https://sparky.example:8443"
    assert _profile_data(enabled)[CONF_SPARKY_API_KEY] == "secret"


@pytest.mark.parametrize("person", ["light.living_room", "person.", "not-an-entity"])
def test_profile_rejects_person_entity_values_outside_person_domain(person: str) -> None:
    assert _profile_errors(_profile_input(person_entity_id=person)) == {
        CONF_PERSON: "invalid_person"
    }


def test_profile_stores_canonical_person_entity_id() -> None:
    data = _profile_data(_profile_input(person_entity_id="PERSON.Frank"))

    assert data[CONF_PERSON] == "person.frank"


async def test_profile_add_trims_title_and_keeps_person_optional() -> None:
    entry = _profile_entry([_profile_subentry()])
    flow = _profile_flow(entry)

    result = await flow.async_step_user(_profile_input(name="  Alex  "))

    assert result["type"] == "create_entry"
    assert result["title"] == "Alex"
    assert result["unique_id"] is None
    assert result["data"][CONF_LEGACY_PROFILE] is False
    assert CONF_PERSON not in result["data"]


async def test_profile_add_rejects_case_insensitive_duplicate_title() -> None:
    entry = _profile_entry([_profile_subentry(title="Primary")])
    flow = _profile_flow(entry)

    result = await flow.async_step_user(_profile_input(name=" primary "))

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_NAME: "duplicate_profile_name"}


async def test_profile_reconfigure_allows_own_name_and_preserves_legacy_flag() -> None:
    subentry = _profile_subentry(title="Primary")
    entry = _profile_entry([subentry])
    flow = _profile_flow(entry, reconfigure_subentry_id=subentry.subentry_id)

    result = await flow.async_step_reconfigure(_profile_input(name=" Primary "))

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    entry_flow = flow.hass.config_entries.async_update_subentry
    entry_flow.assert_called_once()
    call = entry_flow.call_args.kwargs
    assert call["entry"] is entry
    assert call["subentry"] is subentry
    assert call["title"] == "Primary"
    assert call["data"][CONF_LEGACY_PROFILE] is True


async def test_profile_reconfigure_preserves_hidden_sparky_api_key() -> None:
    existing_data = _profile_data(
        _profile_input(
            sparky_enabled=True,
            sparky_url="https://sparky.example/",
            sparky_api_key="secret",
        ),
        legacy_profile=True,
    )
    subentry = _profile_subentry(data=existing_data)
    entry = _profile_entry([subentry])
    flow = _profile_flow(entry, reconfigure_subentry_id=subentry.subentry_id)

    result = await flow.async_step_reconfigure(_profile_input(name="Primary", sparky_enabled=True))

    assert result["type"] == "abort"
    data = flow.hass.config_entries.async_update_subentry.call_args.kwargs["data"]
    assert data[CONF_SPARKY_URL] == "https://sparky.example"
    assert data[CONF_SPARKY_API_KEY] == "secret"


async def test_profile_reconfigure_rejects_another_profiles_title() -> None:
    current = _profile_subentry(subentry_id="current", title="Primary")
    other = _profile_subentry(subentry_id="other", title="Alex")
    entry = _profile_entry([current, other])
    flow = _profile_flow(entry, reconfigure_subentry_id=current.subentry_id)

    result = await flow.async_step_reconfigure(_profile_input(name="alex"))

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {CONF_NAME: "duplicate_profile_name"}
