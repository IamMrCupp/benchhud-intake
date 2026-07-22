"""Status vocabulary, the open/closed split, and the outcome invariant.

The most important test here is `test_open_statuses_is_everything_except_closed`
— it's what stops a newly added status from silently vanishing out of benchhud's
work queue, which would look exactly like a customer's board being forgotten.
"""

import pytest

from intake.schema import _OUTCOMES, _STATUSES
from intake.status import (
    OPEN_STATUSES,
    Outcome,
    Status,
    is_open,
    validate_outcome,
)

# --- vocabulary -------------------------------------------------------------


def test_all_eight_statuses_exist() -> None:
    assert {s.value for s in Status} == {
        "awaiting_arrival",
        "intake",
        "diagnosing",
        "quoted",
        "awaiting_parts",
        "repairing",
        "ready",
        "closed",
    }


def test_all_four_outcomes_exist() -> None:
    assert {o.value for o in Outcome} == {
        "repaired",
        "unrepairable",
        "declined",
        "abandoned",
    }


def test_schema_status_check_matches_the_enum() -> None:
    """The schema duplicates this list; drift would let a status be rejected
    by the database while Python believes it's legal."""
    assert set(_STATUSES) == {s.value for s in Status}


def test_schema_outcome_check_matches_the_enum() -> None:
    assert set(_OUTCOMES) == {o.value for o in Outcome}


# --- open / closed ----------------------------------------------------------


def test_open_statuses_is_everything_except_closed() -> None:
    """Derived, never hand-listed.

    `?status=open` on the Phase 5 API is defined as "not closed". If this set
    were maintained by hand, adding a status and forgetting to list it would
    hide those tickets from the bench queue — indistinguishable, from the
    outside, from losing the ticket.
    """
    assert set(Status) - {Status.CLOSED} == OPEN_STATUSES


def test_every_status_is_classified() -> None:
    """No status may be neither open nor closed."""
    for status in Status:
        assert is_open(status) is (status is not Status.CLOSED)


def test_seven_statuses_are_open() -> None:
    assert len(OPEN_STATUSES) == 7


def test_closed_is_not_open() -> None:
    assert not is_open(Status.CLOSED)
    assert Status.CLOSED not in OPEN_STATUSES


# --- outcome invariant ------------------------------------------------------


def test_closing_requires_an_outcome() -> None:
    with pytest.raises(ValueError):
        validate_outcome(Status.CLOSED, None)


def test_closing_with_an_outcome_is_fine() -> None:
    for outcome in Outcome:
        validate_outcome(Status.CLOSED, outcome)


@pytest.mark.parametrize("status", sorted(OPEN_STATUSES, key=lambda s: s.value))
def test_open_statuses_forbid_an_outcome(status) -> None:
    """An outcome means the job is finished. Carrying one while open is a lie."""
    with pytest.raises(ValueError):
        validate_outcome(status, Outcome.REPAIRED)


@pytest.mark.parametrize("status", sorted(OPEN_STATUSES, key=lambda s: s.value))
def test_open_statuses_accept_no_outcome(status) -> None:
    validate_outcome(status, None)


# --- parsing ----------------------------------------------------------------


def test_status_parses_from_its_stored_value() -> None:
    assert Status("diagnosing") is Status.DIAGNOSING


def test_unknown_status_value_raises() -> None:
    with pytest.raises(ValueError):
        Status("diagnosin")


def test_unknown_outcome_value_raises() -> None:
    with pytest.raises(ValueError):
        Outcome("fixed-ish")
