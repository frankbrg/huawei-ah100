import pytest

from custom_components.huawei_ah100.plausibility import (
    Outcome,
    WeightReference,
    check_weight,
    matching_profiles,
)

REFERENCES = {
    "alice": WeightReference(60.0),
    "bob": WeightReference(80.0),
    "carol": None,
    "dave": WeightReference(100.0, initial=True),
}


@pytest.mark.parametrize(
    ("weight", "profile_id", "verified", "expected"),
    [
        pytest.param(63.0, "alice", False, (Outcome.ACCEPTED, "alice"), id="within_5_percent"),
        pytest.param(65.0, "alice", True, (Outcome.ACCEPTED, "alice"), id="verified_10_percent"),
        pytest.param(
            110.0, "dave", False, (Outcome.ACCEPTED, "dave"), id="initial_hint_10_percent"
        ),
        pytest.param(140.0, "carol", False, (Outcome.ACCEPTED, "carol"), id="stale_reference"),
        pytest.param(78.0, "alice", False, (Outcome.REDIRECTED, "bob"), id="fits_one_other"),
        pytest.param(67.0, "alice", True, (Outcome.REJECTED, None), id="verified_beyond_10"),
        pytest.param(70.0, "alice", False, (Outcome.REJECTED, None), id="fits_nobody"),
        pytest.param(92.0, "bob", False, (Outcome.REDIRECTED, "dave"), id="other_initial_hint"),
    ],
)
def test_check_weight(
    weight: float, profile_id: str, verified: bool, expected: tuple[Outcome, str | None]
) -> None:
    assert check_weight(weight, profile_id, REFERENCES, verified=verified) == expected


def test_two_fitting_profiles_are_ambiguous() -> None:
    references = {
        "alice": WeightReference(60.0),
        "bob": WeightReference(80.0),
        "eve": WeightReference(81.0),
    }

    assert check_weight(80.5, "alice", references, verified=False) == (Outcome.REJECTED, None)


@pytest.mark.parametrize(
    ("weight", "expected"),
    [
        pytest.param(62.0, ["alice"], id="one_profile"),
        pytest.param(79.0, ["bob"], id="stale_profile_is_skipped"),
        pytest.param(105.0, ["dave"], id="initial_hint_10_percent"),
        pytest.param(70.0, [], id="nobody"),
    ],
)
def test_matching_profiles(weight: float, expected: list[str]) -> None:
    assert matching_profiles(weight, REFERENCES) == expected
