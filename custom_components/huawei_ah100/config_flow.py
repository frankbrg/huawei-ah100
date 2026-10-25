from __future__ import annotations

import math
import re
from datetime import date
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .const import (
    CONF_ALLOW_BIND,
    CONF_BIRTH_DATE,
    CONF_HEIGHT,
    CONF_INITIAL_WEIGHT,
    CONF_SEX,
    CONF_USER_ID,
    DOMAIN,
    NAME,
)

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak


_MAC_RE = re.compile(r"^(?:[0-9A-F]{2}:){5}[0-9A-F]{2}$")
_MAC_UNSEPARATED_RE = re.compile(r"^[0-9A-F]{12}$")
_MAC_HYPHENATED_RE = re.compile(r"^(?:[0-9A-F]{2}-){5}[0-9A-F]{2}$")


def _normalize_address(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    address = value.strip().upper()
    if _MAC_UNSEPARATED_RE.fullmatch(address):
        return ":".join(address[index : index + 2] for index in range(0, 12, 2))
    if _MAC_HYPHENATED_RE.fullmatch(address):
        address = address.replace("-", ":")
    return address if _MAC_RE.fullmatch(address) else None


def _profile_schema(*, include_address: bool = False) -> probatio.Schema:
    fields: dict[Any, Any] = {}
    if include_address:
        fields[probatio.Required(CONF_ADDRESS)] = selector({"text": {"type": "text"}})
    fields.update(
        {
            probatio.Required(CONF_BIRTH_DATE): selector({"date": {}}),
            probatio.Required(CONF_HEIGHT): selector(
                {"number": {"min": 1, "max": 255, "step": 1, "mode": "box"}}
            ),
            probatio.Required(CONF_SEX): selector(
                {
                    "select": {
                        "options": ["male", "female"],
                        "mode": "dropdown",
                        "translation_key": "sex",
                    }
                }
            ),
            probatio.Required(CONF_INITIAL_WEIGHT): selector(
                {"number": {"min": 0, "max": 300, "step": "any", "mode": "box"}}
            ),
            probatio.Required(CONF_USER_ID, default=1): selector(
                {"number": {"min": 0, "max": 255, "step": 1, "mode": "box"}}
            ),
            probatio.Required(CONF_ALLOW_BIND, default=False): selector({"boolean": {}}),
        }
    )
    return probatio.Schema(fields)


USER_SCHEMA = _profile_schema(include_address=True)
BLUETOOTH_SCHEMA = _profile_schema()
OPTIONS_SCHEMA = _profile_schema()


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _profile_errors(user_input: dict[str, Any], today: date | None = None) -> dict[str, str]:
    errors: dict[str, str] = {}
    today = today or date.today()

    birth_date = _as_date(user_input.get(CONF_BIRTH_DATE))
    if birth_date is None:
        errors[CONF_BIRTH_DATE] = "invalid_birth_date"
    elif birth_date > today:
        errors[CONF_BIRTH_DATE] = "future_birth_date"
    elif (
        today.year
        - birth_date.year
        - ((today.month, today.day) < (birth_date.month, birth_date.day))
        > 127
    ):
        errors[CONF_BIRTH_DATE] = "age_out_of_range"

    height = user_input.get(CONF_HEIGHT)
    if isinstance(height, bool) or not isinstance(height, (int, float)):
        errors[CONF_HEIGHT] = "invalid_height"
    elif not 1 <= height <= 255 or not math.isfinite(height):
        errors[CONF_HEIGHT] = "invalid_height"
    elif not float(height).is_integer():
        errors[CONF_HEIGHT] = "height_not_integer"

    sex = user_input.get(CONF_SEX)
    if sex not in ("male", "female"):
        errors[CONF_SEX] = "invalid_sex"

    weight = user_input.get(CONF_INITIAL_WEIGHT)
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not 0 < weight <= 300
        or not math.isfinite(weight)
    ):
        errors[CONF_INITIAL_WEIGHT] = "invalid_weight"

    user_id = user_input.get(CONF_USER_ID)
    if isinstance(user_id, bool) or not isinstance(user_id, (int, float)):
        errors[CONF_USER_ID] = "invalid_user_id"
    elif not 0 <= user_id <= 255 or not math.isfinite(user_id):
        errors[CONF_USER_ID] = "invalid_user_id"
    elif not float(user_id).is_integer():
        errors[CONF_USER_ID] = "user_id_not_integer"

    if not isinstance(user_input.get(CONF_ALLOW_BIND), bool):
        errors[CONF_ALLOW_BIND] = "invalid_allow_bind"

    return errors


def _profile_data(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_BIRTH_DATE: _as_date(user_input[CONF_BIRTH_DATE]).isoformat(),
        CONF_HEIGHT: int(user_input[CONF_HEIGHT]),
        CONF_SEX: user_input[CONF_SEX],
        CONF_INITIAL_WEIGHT: float(user_input[CONF_INITIAL_WEIGHT]),
        CONF_USER_ID: int(user_input[CONF_USER_ID]),
        CONF_ALLOW_BIND: user_input[CONF_ALLOW_BIND],
    }


class HuaweiAH100ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a Huawei AH100 scale."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            address = _normalize_address(user_input.get(CONF_ADDRESS))
            if address is None:
                errors[CONF_ADDRESS] = "invalid_mac"
            errors.update(_profile_errors(user_input))

            if not errors:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=NAME,
                    data={CONF_ADDRESS: address, **_profile_data(user_input)},
                )

        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak | dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if isinstance(discovery_info, dict):
            address = getattr(self, "_bluetooth_address", None)
            if address is None:
                return self.async_abort(reason="invalid_address")
            errors = _profile_errors(discovery_info)
            if errors:
                return self.async_show_form(
                    step_id="bluetooth", data_schema=BLUETOOTH_SCHEMA, errors=errors
                )
            return self.async_create_entry(
                title=NAME,
                data={CONF_ADDRESS: address, **_profile_data(discovery_info)},
            )

        if discovery_info is None:
            return self.async_abort(reason="invalid_address")
        if (
            not discovery_info.connectable
            or not isinstance(discovery_info.name, str)
            or not discovery_info.name.startswith("CH100")
        ):
            return self.async_abort(reason="unsupported_device")
        address = _normalize_address(discovery_info.address)
        if address is None:
            return self.async_abort(reason="invalid_address")

        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._bluetooth_address = address
        return self.async_show_form(step_id="bluetooth", data_schema=BLUETOOTH_SCHEMA)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HuaweiAH100OptionsFlow()


class HuaweiAH100OptionsFlow(OptionsFlow):
    """Update the Huawei AH100 user profile and pairing preference."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _profile_errors(user_input)
            if not errors:
                return self.async_create_entry(title="", data=_profile_data(user_input))

        suggestions = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(OPTIONS_SCHEMA, suggestions),
            errors=errors,
        )
