"""Strings are accepted wherever an enum is.

Every real caller hands the store strings — the Phase 2 form posts them, the
Phase 5 API takes them as query params. Found in acceptance testing: the unit
tests only ever passed enum members, so this path was unexercised, and the
`is` comparisons in `validate_outcome` meant a string status silently skipped
the outcome invariant rather than failing loudly.
"""

import pytest

from intake.db import connect
from intake.schema import initialise
from intake.status import Outcome, Status, validate_outcome
from intake.store import TicketStore


@pytest.fixture
def store(tmp_path):
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)
        yield TicketStore(conn, year=26)


@pytest.fixture
def ticket(store):
    return store.create_ticket(device="CDJ-3000", customer_name="DJ", phone="555-0100")


# --- validate_outcome must not be fooled by a string ------------------------


def test_validate_outcome_rejects_a_string_closed_without_outcome() -> None:
    """The silent bypass. `"closed" is Status.CLOSED` is False, so an `is`-based
    check waved this straight through."""
    with pytest.raises(ValueError):
        validate_outcome("closed", None)


def test_validate_outcome_rejects_a_string_open_with_outcome() -> None:
    with pytest.raises(ValueError):
        validate_outcome("repairing", "repaired")


def test_validate_outcome_accepts_valid_strings() -> None:
    validate_outcome("closed", "repaired")
    validate_outcome("diagnosing", None)


# --- set_status -------------------------------------------------------------


def test_set_status_accepts_a_string(store, ticket) -> None:
    assert store.set_status(ticket.ref, "diagnosing").status is Status.DIAGNOSING


def test_set_status_accepts_a_string_outcome(store, ticket) -> None:
    closed = store.set_status(ticket.ref, "closed", outcome="repaired")

    assert closed.status is Status.CLOSED
    assert closed.outcome is Outcome.REPAIRED


def test_string_close_without_outcome_raises_readably(store, ticket) -> None:
    """Must be the domain error, not AttributeError and not IntegrityError."""
    with pytest.raises(ValueError, match="requires an outcome"):
        store.set_status(ticket.ref, "closed")


def test_string_open_status_with_outcome_raises(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.set_status(ticket.ref, "repairing", outcome="repaired")


def test_unknown_status_string_raises(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.set_status(ticket.ref, "diagnosin")


def test_unknown_outcome_string_raises(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.set_status(ticket.ref, "closed", outcome="fixed-ish")


def test_enum_members_still_work(store, ticket) -> None:
    assert store.set_status(ticket.ref, Status.READY).status is Status.READY


# --- list_tickets -----------------------------------------------------------


def test_list_filters_by_string_status(store, ticket) -> None:
    store.set_status(ticket.ref, "diagnosing")

    assert [t.ref for t in store.list_tickets(status="diagnosing")] == [ticket.ref]


def test_list_rejects_an_unknown_status_string(store) -> None:
    with pytest.raises(ValueError):
        store.list_tickets(status="diagnosin")


# --- the whole point: no invalid row can reach the database ----------------


def test_string_path_cannot_produce_a_closed_ticket_without_outcome(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.set_status(ticket.ref, "closed")

    row = store.conn.execute("SELECT status, outcome FROM tickets").fetchone()
    assert row["status"] == "intake"
    assert row["outcome"] is None
