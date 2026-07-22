"""Creating and reading tickets.

The store is the layer Phase 2's form and Phase 5's API both sit on, so its
guarantees have to hold regardless of which caller is asking — a rule enforced
only in the web form is not a rule.
"""

import pytest

from intake.db import connect
from intake.schema import initialise
from intake.status import Status
from intake.store import TicketStore


@pytest.fixture
def store(tmp_path):
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)
        yield TicketStore(conn, year=26)


def _create(store: TicketStore, **overrides):
    fields = {
        "device": "ThinkPad X1",
        "symptoms": "no power",
        "customer_name": "A. Customer",
        "phone": "555-0100",
    }
    fields.update(overrides)
    return store.create_ticket(**fields)


# --- creation ---------------------------------------------------------------


def test_first_ticket_of_the_year_is_0001(store) -> None:
    assert _create(store).ref == "26-0001"


def test_refs_increment(store) -> None:
    assert [_create(store).ref for _ in range(3)] == ["26-0001", "26-0002", "26-0003"]


def test_new_tickets_start_at_intake(store) -> None:
    """Logged at the bench and not yet looked at."""
    assert _create(store).status is Status.INTAKE


def test_new_tickets_have_no_outcome(store) -> None:
    assert _create(store).outcome is None


def test_timestamps_are_stamped_on_create(store) -> None:
    ticket = _create(store)

    assert ticket.created_at
    assert ticket.updated_at == ticket.created_at


def test_optional_fields_default_to_none(store) -> None:
    ticket = _create(store)

    assert ticket.serial is None
    assert ticket.return_address is None
    assert ticket.accessories is None


def test_all_fields_round_trip(store) -> None:
    ticket = _create(
        store,
        device="MacBook Pro 16",
        serial="C02X",
        symptoms="liquid damage",
        customer_name="DJ in LA",
        phone="555-0111",
        email="dj@example.com",
        return_address="1 Sunset Blvd\nLos Angeles",
        accessories="charger, no drive",
        condition_notes="dent on lid corner",
    )

    fetched = store.get_by_ref(ticket.ref)
    assert fetched == ticket
    assert fetched.return_address == "1 Sunset Blvd\nLos Angeles"
    assert fetched.condition_notes == "dent on lid corner"


# --- the contact rule is a store guarantee, not a form one ------------------


def test_rejects_neither_phone_nor_email(store) -> None:
    with pytest.raises(ValueError):
        _create(store, phone=None, email=None)


def test_phone_alone_is_enough(store) -> None:
    assert _create(store, phone="555-0100", email=None).ref


def test_email_alone_is_enough(store) -> None:
    assert _create(store, phone=None, email="a@example.com").ref


def test_rejected_create_does_not_burn_a_ref(store) -> None:
    """A validation failure shouldn't leave a hole in the numbering."""
    with pytest.raises(ValueError):
        _create(store, phone=None, email=None)

    assert _create(store).ref == "26-0001"


def test_requires_a_customer_name(store) -> None:
    with pytest.raises(ValueError):
        _create(store, customer_name="")


def test_requires_a_device(store) -> None:
    with pytest.raises(ValueError):
        _create(store, device="  ")


# --- lookup -----------------------------------------------------------------


def test_get_by_ref_returns_the_ticket(store) -> None:
    created = _create(store)

    assert store.get_by_ref("26-0001") == created


def test_get_by_ref_returns_none_when_missing(store) -> None:
    """benchhud's get_ticket() treats a miss as a normal 404, not an error."""
    assert store.get_by_ref("26-9999") is None


def test_get_by_ref_tolerates_whitespace(store) -> None:
    _create(store)

    assert store.get_by_ref(" 26-0001 ") is not None


def test_get_by_ref_rejects_a_malformed_ref(store) -> None:
    with pytest.raises(ValueError):
        store.get_by_ref("nonsense")


# --- listing ----------------------------------------------------------------


def test_list_returns_everything_by_default(store) -> None:
    for _ in range(3):
        _create(store)

    assert len(store.list_tickets()) == 3


def test_list_is_empty_on_a_fresh_store(store) -> None:
    assert store.list_tickets() == []


def _force_status(store: TicketStore, ref: str, status: str, outcome: str | None = None) -> None:
    """Set a status over raw SQL.

    Transitions are C.2's job; this item is create-and-read only. Going around
    the (not yet written) setter keeps the listing tests honest about what
    they're exercising.
    """
    store.conn.execute(
        "UPDATE tickets SET status = ?, outcome = ? WHERE year || '-' || counter = ?",
        (status, outcome, ref.split("-")[0] + "-" + str(int(ref.split("-")[1]))),
    )
    store.conn.commit()


def test_list_filters_by_status(store) -> None:
    first = _create(store)
    _create(store)
    _force_status(store, first.ref, "diagnosing")

    assert [t.ref for t in store.list_tickets(status=Status.DIAGNOSING)] == [first.ref]


def test_list_open_only_excludes_closed(store) -> None:
    """The set Phase 5 serves as ?status=open."""
    open_ticket = _create(store)
    closed = _create(store)
    _force_status(store, closed.ref, "closed", "repaired")

    refs = [t.ref for t in store.list_tickets(open_only=True)]
    assert refs == [open_ticket.ref]


def test_list_is_newest_first(store) -> None:
    """The bench wants what just came in, not what came in first."""
    refs = [_create(store).ref for _ in range(3)]

    assert [t.ref for t in store.list_tickets()] == list(reversed(refs))
