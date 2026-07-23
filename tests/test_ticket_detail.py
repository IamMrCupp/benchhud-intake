"""The ticket detail page and its status control.

Unlike the list, this page *does* show contact details — it's the one you
navigate to on purpose, and it's where you find the number to ring someone
about their board.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in
from intake.app import app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.status import Status
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
    # Auth gates everything but /healthz and /login, so a client testing page
    # content has to sign in. The gate itself is tested in test_auth_required.
    yield sign_in(TestClient(app))
    app.dependency_overrides.clear()


@pytest.fixture
def store(db_path):
    with connect(db_path) as conn:
        yield TicketStore(conn, year=26)


@pytest.fixture
def ticket(store):
    return store.create_ticket(
        device="Pioneer CDJ-3000",
        serial="ABC123",
        symptoms="jog wheel unresponsive",
        customer_name="DJ in LA",
        phone="555-0100",
        email="dj@example.com",
        return_address="1 Sunset Blvd\nLos Angeles, CA",
        accessories="no power brick",
        condition_notes="scuff on faceplate",
    )


# --- the page ---------------------------------------------------------------


def test_page_renders(client, ticket) -> None:
    response = client.get(f"/tickets/{ticket.ref}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize(
    "value",
    [
        "26-0001",
        "Pioneer CDJ-3000",
        "ABC123",
        "jog wheel unresponsive",
        "DJ in LA",
        "no power brick",
        "scuff on faceplate",
    ],
)
def test_ticket_fields_are_shown(client, ticket, value) -> None:
    assert value in client.get(f"/tickets/{ticket.ref}").text


@pytest.mark.parametrize("value", ["555-0100", "dj@example.com", "1 Sunset Blvd"])
def test_contact_details_are_shown_here(client, ticket, value) -> None:
    """The opposite of the list page — this is where you find the number to
    ring someone about their board."""
    assert value in client.get(f"/tickets/{ticket.ref}").text


def test_links_back_to_the_list(client, ticket) -> None:
    assert 'href="/tickets"' in client.get(f"/tickets/{ticket.ref}").text


def test_unknown_ref_404s(client) -> None:
    assert client.get("/tickets/26-9999").status_code == 404


def test_malformed_ref_404s(client) -> None:
    assert client.get("/tickets/nonsense").status_code == 404


# --- the status control -----------------------------------------------------


def test_every_status_is_offered(client, ticket) -> None:
    body = client.get(f"/tickets/{ticket.ref}").text

    for status in Status:
        assert f'value="{status.value}"' in body


def test_outcome_choice_is_present(client, ticket) -> None:
    """Visible alongside the status control, so 'closing needs an outcome' is
    apparent before submitting rather than after."""
    body = client.get(f"/tickets/{ticket.ref}").text

    assert 'name="outcome"' in body
    assert "repaired" in body


def test_changing_status_persists(client, store, ticket) -> None:
    client.post(f"/tickets/{ticket.ref}/status", data={"status": "diagnosing", "outcome": ""})

    assert store.get_by_ref(ticket.ref).status is Status.DIAGNOSING


def test_status_change_redirects_back(client, ticket) -> None:
    """POST/redirect/GET, so a refresh doesn't re-submit the transition."""
    response = client.post(
        f"/tickets/{ticket.ref}/status",
        data={"status": "diagnosing", "outcome": ""},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/tickets/{ticket.ref}"


def test_closing_with_an_outcome_works(client, store, ticket) -> None:
    client.post(f"/tickets/{ticket.ref}/status", data={"status": "closed", "outcome": "repaired"})
    closed = store.get_by_ref(ticket.ref)

    assert closed.status is Status.CLOSED
    assert closed.outcome == "repaired"


def test_closing_without_an_outcome_is_refused(client, store, ticket) -> None:
    response = client.post(
        f"/tickets/{ticket.ref}/status", data={"status": "closed", "outcome": ""}
    )

    assert response.status_code == 400
    assert "requires an outcome" in response.text
    assert store.get_by_ref(ticket.ref).status is Status.INTAKE


def test_reopening_clears_the_outcome(client, store, ticket) -> None:
    client.post(f"/tickets/{ticket.ref}/status", data={"status": "closed", "outcome": "repaired"})

    client.post(f"/tickets/{ticket.ref}/status", data={"status": "diagnosing", "outcome": ""})

    reopened = store.get_by_ref(ticket.ref)
    assert reopened.status is Status.DIAGNOSING
    assert reopened.outcome is None


def test_unknown_status_is_refused(client, store, ticket) -> None:
    response = client.post(
        f"/tickets/{ticket.ref}/status", data={"status": "diagnosin", "outcome": ""}
    )

    assert response.status_code == 400
    assert store.get_by_ref(ticket.ref).status is Status.INTAKE


def test_status_change_on_unknown_ref_404s(client) -> None:
    response = client.post("/tickets/26-9999/status", data={"status": "diagnosing", "outcome": ""})

    assert response.status_code == 404


def test_an_outcome_on_an_open_status_is_refused(client, store, ticket) -> None:
    """The store rejects this; the page must surface it rather than 500."""
    response = client.post(
        f"/tickets/{ticket.ref}/status", data={"status": "repairing", "outcome": "repaired"}
    )

    assert response.status_code == 400
    assert store.get_by_ref(ticket.ref).status is Status.INTAKE


# --- escaping ---------------------------------------------------------------


def test_values_are_escaped(client, store) -> None:
    store.create_ticket(
        device="<script>alert(1)</script>", customer_name="Nobody", phone="555-0100"
    )

    body = client.get("/tickets/26-0001").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
