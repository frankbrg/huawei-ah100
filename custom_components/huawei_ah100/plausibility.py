"""Check that a weighing fits the profile it is about to be stored for."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

TOLERANCE = 0.05
VERIFIED_TOLERANCE = 0.10
REFERENCE_MAX_AGE = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class WeightReference:
    """Weight a profile is expected to be near."""

    weight_kg: float
    # The initial weight is only a hint entered during setup, so it gets the wider tolerance.
    initial: bool = False


class Outcome(StrEnum):
    """What happens to a weighing after the plausibility check."""

    ACCEPTED = "accepted"
    REDIRECTED = "redirected"
    REJECTED = "rejected"


def _fits(weight_kg: float, reference: WeightReference, tolerance: float) -> bool:
    if reference.initial:
        tolerance = max(tolerance, VERIFIED_TOLERANCE)
    return abs(weight_kg - reference.weight_kg) <= reference.weight_kg * tolerance


def check_weight(
    weight_kg: float,
    profile_id: str,
    references: Mapping[str, WeightReference | None],
    *,
    verified: bool,
) -> tuple[Outcome, str | None]:
    """Return the outcome and the profile that should receive the weighing.

    A missing reference means the profile's last weighing is too old to judge, so the
    weighing is accepted. A mismatch moves to the only other profile that fits within
    the strict tolerance, otherwise it is rejected.
    """
    reference = references.get(profile_id)
    if reference is None or _fits(
        weight_kg, reference, VERIFIED_TOLERANCE if verified else TOLERANCE
    ):
        return Outcome.ACCEPTED, profile_id
    candidates = [
        other_id
        for other_id, other in references.items()
        if other_id != profile_id and other is not None and _fits(weight_kg, other, TOLERANCE)
    ]
    if len(candidates) == 1:
        return Outcome.REDIRECTED, candidates[0]
    return Outcome.REJECTED, None


def matching_profiles(
    weight_kg: float, references: Mapping[str, WeightReference | None]
) -> list[str]:
    """Return the profiles a weighing without any profile selection could belong to."""
    return [
        profile_id
        for profile_id, reference in references.items()
        if reference is not None and _fits(weight_kg, reference, TOLERANCE)
    ]
