"""The ticket list — how work gets found again after intake.

One test here is about privacy rather than function: this is the screen most
likely to be left open on a shared display or read over a shoulder, and it does
its job perfectly well without any contact details on it.
"""

import pytest
from fastapi.testclient import TestClient

from intake.app import app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def client(db_path):
    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def store(db_path):
    with connect(db_path) as conn:
        yield TicketStore(conn, year=26)


@pytest.fixture
def tickets(store):
    """Three tickets: one open, one mid-repair, one closed."""
    a = store.create_ticket(
        device="Pioneer CDJ-3000",
        customer_name="DJ in LA",
        email="dj@example.com",
        return_address="1 Sunset Blvd",
        symptoms="jog wheel unresponsive",
    )
    b = store.create_ticket(
        device="ThinkPad X1", customer_name="Family", phone="555-0100", symptoms="no power"
    )
    c = store.create_ticket(device="RTX 3080", customer_name="Local DJ", phone="555-0111")
    store.set_status(b.ref, "repairing")
    store.set_status(c.ref, "closed", outcome="repaired")
    return a, b, c


# --- what's listed ----------------------------------------------------------


def test_page_renders(client) -> None:
    response = client.get("/tickets")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_open_tickets_are_listed(client, tickets) -> None:
    a, b, _ = tickets
    body = client.get("/tickets").text

    assert a.ref in body
    assert b.ref in body


def test_closed_tickets_are_hidden_by_default(client, tickets) -> None:
    _, _, closed = tickets

    assert closed.ref not in client.get("/tickets").text


def test_device_is_shown(client, tickets) -> None:
    """What you scan for with a board in your hand."""
    assert "Pioneer CDJ-3000" in client.get("/tickets").text


def test_status_is_shown(client, tickets) -> None:
    assert "repairing" in client.get("/tickets").text


def test_rows_link_to_the_detail_page(client, tickets) -> None:
    a, _, _ = tickets

    assert f'href="/tickets/{a.ref}"' in client.get("/tickets").text


def test_newest_first(client, tickets) -> None:
    a, b, _ = tickets
    body = client.get("/tickets").text

    assert body.index(b.ref) < body.index(a.ref)


# --- filtering --------------------------------------------------------------


def test_status_filter_shows_closed(client, tickets) -> None:
    _, _, closed = tickets

    assert closed.ref in client.get("/tickets?status=closed").text


def test_status_filter_excludes_others(client, tickets) -> None:
    a, _, _ = tickets

    assert a.ref not in client.get("/tickets?status=closed").text


def test_unknown_status_is_a_400_not_a_500(client) -> None:
    """The store raises ValueError for an unknown status; a bad query string is
    the caller's mistake, not a server fault."""
    assert client.get("/tickets?status=diagnosin").status_code == 400


def test_empty_state_is_not_a_blank_page(client) -> None:
    body = client.get("/tickets").text

    assert "no open tickets" in body.lower()


# --- privacy ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("phone", "555-0100"),
        ("email", "dj@example.com"),
        ("return address", "1 Sunset Blvd"),
    ],
)
def test_contact_details_are_not_on_the_list(client, tickets, label, value) -> None:
    """This screen gets left open on a bench. It doesn't need PII to be useful —
    the customer name is enough to recognise a ticket."""
    assert value not in client.get("/tickets").text, f"{label} leaked onto the list page"
