"""Submitting the intake form.

**These tests post real form data**, not constructed keyword arguments. That
distinction is the whole lesson from Phase 1's acceptance testing: a browser
sends strings, and an untouched optional field sends `""` rather than omitting
itself. Tests that build typed values by hand say nothing about the path a
tablet actually takes.
"""

import pytest
from fastapi.testclient import TestClient

from intake.app import app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore

FULL_FORM = {
    "customer_name": "DJ in LA",
    "phone": "",
    "email": "dj@example.com",
    "return_address": "1 Sunset Blvd\nLos Angeles, CA",
    "device": "Pioneer CDJ-3000",
    "serial": "ABC123",
    "symptoms": "jog wheel unresponsive",
    "accessories": "no power brick",
    "condition_notes": "scuff on faceplate",
}

MINIMAL_FORM = {
    "customer_name": "Family",
    "phone": "555-0100",
    "email": "",
    "return_address": "",
    "device": "ThinkPad X1",
    "serial": "",
    "symptoms": "",
    "accessories": "",
    "condition_notes": "",
}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def client(db_path):
    """Override the store with one opened per request, exactly as production does.

    A single shared connection would fail here — FastAPI runs sync endpoints in
    a threadpool and SQLite objects can't cross threads. Mirroring the real
    per-request lifecycle keeps the tests honest about that.
    """

    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def store(db_path):
    """A separate connection for assertions, as an outside observer would have."""
    with connect(db_path) as conn:
        yield TicketStore(conn, year=26)


# --- creating ---------------------------------------------------------------


def test_posting_the_form_creates_a_ticket(client, store) -> None:
    client.post("/intake", data=FULL_FORM)

    assert len(store.list_tickets()) == 1


def test_first_ticket_gets_the_first_ref(client, store) -> None:
    client.post("/intake", data=FULL_FORM)

    assert store.list_tickets()[0].ref == "26-0001"


def test_every_field_is_stored(client, store) -> None:
    client.post("/intake", data=FULL_FORM)
    ticket = store.get_by_ref("26-0001")

    assert ticket.customer_name == "DJ in LA"
    assert ticket.email == "dj@example.com"
    assert ticket.device == "Pioneer CDJ-3000"
    assert ticket.serial == "ABC123"
    assert ticket.symptoms == "jog wheel unresponsive"
    assert ticket.accessories == "no power brick"
    assert ticket.condition_notes == "scuff on faceplate"
    assert ticket.return_address == "1 Sunset Blvd\nLos Angeles, CA"


def test_blank_optional_fields_become_null_not_empty_string(client, store) -> None:
    """A browser posts '' for an untouched field. Storing that as '' would make
    `IS NULL` checks lie and put empty strings in front of a customer."""
    client.post("/intake", data=MINIMAL_FORM)
    ticket = store.get_by_ref("26-0001")

    assert ticket.email is None
    assert ticket.serial is None
    assert ticket.return_address is None
    assert ticket.accessories is None
    assert ticket.condition_notes is None


def test_phone_only_intake_is_accepted(client, store) -> None:
    client.post("/intake", data=MINIMAL_FORM)

    assert store.get_by_ref("26-0001").phone == "555-0100"


def test_new_tickets_start_at_intake(client, store) -> None:
    client.post("/intake", data=FULL_FORM)

    assert store.get_by_ref("26-0001").status == "intake"


# --- POST / redirect / GET --------------------------------------------------


def test_submission_redirects(client) -> None:
    response = client.post("/intake", data=FULL_FORM, follow_redirects=False)

    assert response.status_code in (302, 303)


def test_redirect_points_at_the_confirmation(client) -> None:
    response = client.post("/intake", data=FULL_FORM, follow_redirects=False)

    assert response.headers["location"] == "/intake/26-0001"


def test_refreshing_the_confirmation_creates_nothing(client, store) -> None:
    """The realistic failure: a tablet gets handed over and someone reloads.

    Rendering the confirmation straight from the POST would create a second
    ticket for the same board — a duplicate customer record, which is worse
    than an error message.
    """
    location = client.post("/intake", data=FULL_FORM, follow_redirects=False).headers["location"]

    for _ in range(3):
        assert client.get(location).status_code == 200

    assert len(store.list_tickets()) == 1


# --- the confirmation page --------------------------------------------------


def test_confirmation_shows_the_ref(client) -> None:
    client.post("/intake", data=FULL_FORM)

    assert "26-0001" in client.get("/intake/26-0001").text


def test_confirmation_shows_the_device(client) -> None:
    """Enough context to know you're looking at the right ticket."""
    client.post("/intake", data=FULL_FORM)

    assert "Pioneer CDJ-3000" in client.get("/intake/26-0001").text


def test_confirmation_links_to_a_new_intake(client) -> None:
    """Two boards often arrive together."""
    client.post("/intake", data=FULL_FORM)

    assert 'href="/intake"' in client.get("/intake/26-0001").text


def test_confirmation_404s_for_an_unknown_ref(client) -> None:
    assert client.get("/intake/26-9999").status_code == 404


def test_confirmation_404s_for_a_malformed_ref(client) -> None:
    """A bad ref in the URL is a 404, not a 500."""
    assert client.get("/intake/nonsense").status_code == 404


# --- awkward input ----------------------------------------------------------


def test_unicode_survives_the_round_trip(client, store) -> None:
    client.post("/intake", data={**MINIMAL_FORM, "customer_name": "Renée Ünicode 日本"})

    assert store.get_by_ref("26-0001").customer_name == "Renée Ünicode 日本"


def test_html_in_input_is_escaped_not_executed(client, store) -> None:
    """Condition notes are free text typed by a human at a bench."""
    client.post("/intake", data={**MINIMAL_FORM, "device": "<script>alert(1)</script>"})

    page = client.get("/intake/26-0001").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


# Invalid submissions are A.3's item: the store raises a readable ValueError,
# but turning that into a re-rendered form with the typed values preserved is a
# piece of work in its own right. Until then invalid input is an unhandled 500.
# Tests for it live in tests/test_intake_validation.py.
