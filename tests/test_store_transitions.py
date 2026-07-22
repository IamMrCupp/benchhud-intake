"""Updating tickets and moving them through the workflow.

The transition graph is deliberately permissive between open states — work
doesn't move in a straight line, and modelling a process the shop doesn't follow
would just mean fighting the tool. What *is* enforced is the outcome invariant
and the contact rule, because those protect data rather than order.
"""

import time

import pytest

from intake.db import connect
from intake.schema import initialise
from intake.status import Outcome, Status
from intake.store import TicketStore


@pytest.fixture
def store(tmp_path):
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)
        yield TicketStore(conn, year=26)


@pytest.fixture
def ticket(store):
    return store.create_ticket(
        device="Pioneer CDJ-3000",
        symptoms="jog wheel unresponsive",
        customer_name="DJ in LA",
        email="dj@example.com",
    )


# --- status transitions -----------------------------------------------------


def test_moves_between_open_states(store, ticket) -> None:
    updated = store.set_status(ticket.ref, Status.DIAGNOSING)

    assert updated.status is Status.DIAGNOSING
    assert store.get_by_ref(ticket.ref).status is Status.DIAGNOSING


def test_full_lifecycle(store, ticket) -> None:
    """The path a board actually takes, start to finish."""
    for status in (
        Status.DIAGNOSING,
        Status.QUOTED,
        Status.AWAITING_PARTS,
        Status.REPAIRING,
        Status.READY,
    ):
        assert store.set_status(ticket.ref, status).status is status

    closed = store.set_status(ticket.ref, Status.CLOSED, outcome=Outcome.REPAIRED)
    assert closed.status is Status.CLOSED
    assert closed.outcome is Outcome.REPAIRED


def test_can_go_backwards(store, ticket) -> None:
    """A board comes back, or a part arrives wrong. Not an error."""
    store.set_status(ticket.ref, Status.READY)

    assert store.set_status(ticket.ref, Status.REPAIRING).status is Status.REPAIRING


def test_unknown_ref_raises(store) -> None:
    """Unlike reading, updating something absent is a caller bug."""
    with pytest.raises(LookupError):
        store.set_status("26-9999", Status.DIAGNOSING)


# --- the outcome invariant --------------------------------------------------


def test_closing_without_an_outcome_raises(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.set_status(ticket.ref, Status.CLOSED)


def test_open_status_with_an_outcome_raises(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.set_status(ticket.ref, Status.REPAIRING, outcome=Outcome.REPAIRED)


@pytest.mark.parametrize("outcome", list(Outcome))
def test_every_outcome_can_close_a_ticket(store, ticket, outcome) -> None:
    assert store.set_status(ticket.ref, Status.CLOSED, outcome=outcome).outcome is outcome


def test_reopening_clears_the_outcome(store, ticket) -> None:
    """A reopened ticket isn't finished, so it can't still say how it finished."""
    store.set_status(ticket.ref, Status.CLOSED, outcome=Outcome.REPAIRED)

    reopened = store.set_status(ticket.ref, Status.DIAGNOSING)

    assert reopened.status is Status.DIAGNOSING
    assert reopened.outcome is None
    assert store.get_by_ref(ticket.ref).outcome is None


def test_a_failed_transition_changes_nothing(store, ticket) -> None:
    store.set_status(ticket.ref, Status.REPAIRING)

    with pytest.raises(ValueError):
        store.set_status(ticket.ref, Status.CLOSED)

    assert store.get_by_ref(ticket.ref).status is Status.REPAIRING


# --- timestamps -------------------------------------------------------------


def test_updated_at_advances_and_created_at_does_not(store, ticket) -> None:
    time.sleep(1.1)  # timestamps are second-precision

    updated = store.set_status(ticket.ref, Status.DIAGNOSING)

    assert updated.created_at == ticket.created_at
    assert updated.updated_at > ticket.updated_at


# --- editing intake details -------------------------------------------------


def test_updates_a_single_field(store, ticket) -> None:
    updated = store.update_ticket(ticket.ref, condition_notes="scuff on faceplate")

    assert updated.condition_notes == "scuff on faceplate"
    assert updated.device == ticket.device


def test_updates_several_fields(store, ticket) -> None:
    updated = store.update_ticket(
        ticket.ref,
        serial="ABC123",
        accessories="no power brick",
        symptoms="jog wheel dead on left deck",
    )

    assert (updated.serial, updated.accessories) == ("ABC123", "no power brick")
    assert updated.symptoms == "jog wheel dead on left deck"


def test_update_leaves_other_fields_alone(store, ticket) -> None:
    updated = store.update_ticket(ticket.ref, serial="ABC123")

    assert updated.customer_name == ticket.customer_name
    assert updated.email == ticket.email
    assert updated.status is ticket.status


def test_update_cannot_remove_the_last_contact_method(store, ticket) -> None:
    """The ticket arrived with only an email. Blanking it strands the customer."""
    with pytest.raises(ValueError):
        store.update_ticket(ticket.ref, email="")


def test_update_can_swap_one_contact_method_for_another(store, ticket) -> None:
    updated = store.update_ticket(ticket.ref, email="", phone="555-0111")

    assert (updated.email, updated.phone) == (None, "555-0111")


def test_update_cannot_blank_the_customer_name(store, ticket) -> None:
    with pytest.raises(ValueError):
        store.update_ticket(ticket.ref, customer_name="   ")


def test_update_rejects_an_unknown_field(store, ticket) -> None:
    """Guards against a typo'd kwarg silently doing nothing."""
    with pytest.raises(ValueError):
        store.update_ticket(ticket.ref, custmoer_name="Typo")


def test_update_cannot_change_status(store, ticket) -> None:
    """Status moves through set_status, where the outcome invariant is enforced."""
    with pytest.raises(ValueError):
        store.update_ticket(ticket.ref, status="closed")


def test_update_bumps_updated_at(store, ticket) -> None:
    time.sleep(1.1)

    updated = store.update_ticket(ticket.ref, serial="ABC123")

    assert updated.updated_at > ticket.updated_at
    assert updated.created_at == ticket.created_at


def test_update_on_unknown_ref_raises(store) -> None:
    with pytest.raises(LookupError):
        store.update_ticket("26-9999", serial="ABC123")
