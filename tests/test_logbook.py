from collections.abc import Callable
from types import SimpleNamespace

import pytest
from homeassistant.core import Event

from custom_components.huawei_ah100.logbook import async_describe_events

ENTRY = SimpleNamespace(
    subentries={"p1": SimpleNamespace(title="Alice"), "p2": SimpleNamespace(title="Bob")}
)
HASS = SimpleNamespace(
    config_entries=SimpleNamespace(
        async_get_entry=lambda entry_id: ENTRY if entry_id == "entry" else None
    )
)


def _describers() -> dict[str, Callable[[Event], dict[str, str]]]:
    described = {}
    async_describe_events(
        HASS, lambda _domain, event_type, describe: described.__setitem__(event_type, describe)
    )
    return described


@pytest.mark.parametrize(
    ("event_type", "data", "icon", "message"),
    [
        pytest.param(
            "huawei_ah100_profile_activated",
            {"profile_name": "Bob", "method": "proximity"},
            "mdi:account-check",
            "activated profile Bob by phone proximity",
            id="activated",
        ),
        pytest.param(
            "huawei_ah100_profile_deactivated",
            {"profile_name": "Bob", "method": "selection", "reason": "weighing_saved"},
            "mdi:account-off-outline",
            "deactivated profile Bob (activated by direct selection): weighing saved",
            id="deactivated",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {"weight_kg": 61.0, "assigned_by": "weight", "profile_name": "Alice"},
            "mdi:scale-bathroom",
            "saved 61.0 kg for Alice, assigned by weight",
            id="saved",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {
                "entry_id": "entry",
                "profile_id": "p1",
                "weight_kg": 61.0,
                "assigned_by": "selection",
                "redirected_from": "p2",
            },
            "mdi:scale-bathroom",
            "saved 61.0 kg for Alice, assigned by direct selection, moved from profile Bob",
            id="redirected",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {
                "weight_kg": 95.0,
                "assigned_by": "selection",
                "profile_name": "Bob",
                "weight_check_overridden": True,
            },
            "mdi:scale-bathroom",
            "saved 95.0 kg for Bob, assigned by direct selection, "
            "kept despite the weight check because it was selected directly",
            id="overridden",
        ),
        pytest.param(
            "huawei_ah100_multiple_phones_nearby",
            {"profile_names": ["Alice", "Bob"]},
            "mdi:cellphone-wireless",
            "phones of several profiles nearby (Alice, Bob), "
            "phone proximity cannot choose a profile",
            id="multiple_phones",
        ),
        pytest.param(
            "huawei_ah100_profile_deactivated",
            {"profile_name": "Bob", "method": "proximity", "reason": "multiple_phones_nearby"},
            "mdi:account-off-outline",
            "deactivated profile Bob (activated by phone proximity): "
            "phones of several profiles nearby",
            id="deactivated_by_crowd",
        ),
        pytest.param(
            "huawei_ah100_measurement_rejected",
            {
                "entry_id": "entry",
                "weight_kg": 85.0,
                "reason": "no_matching_profile",
                "candidates": [],
                "nearby_profiles": ["p1", "p2"],
            },
            "mdi:scale-unbalanced",
            "discarded 85.0 kg: weight fits no profile, "
            "phones of several profiles were nearby (Alice, Bob)",
            id="rejected_with_crowd",
        ),
        pytest.param(
            "huawei_ah100_measurement_rejected",
            {
                "entry_id": "entry",
                "weight_kg": 85.0,
                "reason": "ambiguous_weight",
                "candidates": ["p1", "p2"],
            },
            "mdi:scale-unbalanced",
            "discarded 85.0 kg: weight fits several profiles (Alice, Bob)",
            id="rejected",
        ),
        pytest.param(
            "huawei_ah100_measurement_rejected",
            {
                "entry_id": "entry",
                "profile_id": "p2",
                "weight_kg": 95.0,
                "reason": "weight_mismatch",
            },
            "mdi:scale-unbalanced",
            "discarded 95.0 kg for Bob: weight does not fit the profile",
            id="rejected_mismatch",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {"entry_id": "gone", "profile_id": "p9", "weight_kg": 61.0, "assigned_by": "weight"},
            "mdi:scale-bathroom",
            "saved 61.0 kg for p9, assigned by weight",
            id="saved_unknown_profile",
        ),
    ],
)
def test_logbook_describes_assignment_events(
    event_type: str, data: dict[str, object], icon: str, message: str
) -> None:
    assert _describers()[event_type](Event(event_type, data)) == {
        "name": "Huawei AH100",
        "icon": icon,
        "message": message,
    }
