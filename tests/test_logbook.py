from collections.abc import Callable

import pytest
from homeassistant.core import Event

from custom_components.huawei_ah100.logbook import async_describe_events


def _describers() -> dict[str, Callable[[Event], dict[str, str]]]:
    described = {}
    async_describe_events(
        None, lambda _domain, event_type, describe: described.__setitem__(event_type, describe)
    )
    return described


@pytest.mark.parametrize(
    ("event_type", "data", "message"),
    [
        pytest.param(
            "huawei_ah100_profile_activated",
            {"profile_name": "Bob", "method": "proximity"},
            "activated profile Bob by phone proximity",
            id="activated",
        ),
        pytest.param(
            "huawei_ah100_profile_deactivated",
            {"profile_name": "Bob", "method": "selection", "reason": "weighing_saved"},
            "deactivated profile Bob (activated by direct selection): weighing saved",
            id="deactivated",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {"weight_kg": 61.0, "assigned_by": "weight"},
            "saved 61.0 kg, assigned by weight",
            id="saved",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {"weight_kg": 61.0, "assigned_by": "selection", "redirected_from": "bob"},
            "saved 61.0 kg, assigned by direct selection, moved from profile bob",
            id="redirected",
        ),
        pytest.param(
            "huawei_ah100_measurement_saved",
            {"weight_kg": 95.0, "assigned_by": "selection", "weight_check_overridden": True},
            "saved 95.0 kg, assigned by direct selection, "
            "kept despite the weight check because it was selected directly",
            id="overridden",
        ),
        pytest.param(
            "huawei_ah100_multiple_phones_nearby",
            {"profile_names": ["Alice", "Bob"]},
            "phones of several profiles nearby (Alice, Bob), "
            "phone proximity cannot choose a profile",
            id="multiple_phones",
        ),
        pytest.param(
            "huawei_ah100_profile_deactivated",
            {"profile_name": "Bob", "method": "proximity", "reason": "multiple_phones_nearby"},
            "deactivated profile Bob (activated by phone proximity): "
            "phones of several profiles nearby",
            id="deactivated_by_crowd",
        ),
        pytest.param(
            "huawei_ah100_measurement_rejected",
            {"weight_kg": 85.0, "reason": "no_matching_profile", "nearby_profiles": ["a", "b"]},
            "discarded 85.0 kg: weight fits no profile, phones of several profiles were nearby",
            id="rejected_with_crowd",
        ),
        pytest.param(
            "huawei_ah100_measurement_rejected",
            {"weight_kg": 85.0, "reason": "ambiguous_weight"},
            "discarded 85.0 kg: weight fits several profiles",
            id="rejected",
        ),
    ],
)
def test_logbook_describes_assignment_events(
    event_type: str, data: dict[str, object], message: str
) -> None:
    assert _describers()[event_type](Event(event_type, data)) == {
        "name": "Huawei AH100",
        "message": message,
    }
