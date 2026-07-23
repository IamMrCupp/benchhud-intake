"""Rejected submissions come back with the typed values intact.

The person filling this in is standing at a bench with a customer in front of
them. Making them re-type a multi-line return address because they forgot a
phone number is how a tool stops getting used — a worse failure, in practice,
than the validation error itself.
"""

import pytest

from conftest import CsrfClient, sign_in
from intake.app import app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore

FILLED_FORM = {
    "customer_name": "DJ in LA",
    "phone": "",
    "email": "",
    "return_address": "1 Sunset Blvd\nLos Angeles, CA",
    "device": "Pioneer CDJ-3000",
    "serial": "ABC123",
    "symptoms": "jog wheel unresponsive",
    "accessories": "no power brick",
    "condition_notes": "scuff on faceplate",
}


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
    yield sign_in(CsrfClient(app))
    app.dependency_overrides.clear()


@pytest.fixture
def store(db_path):
    with connect(db_path) as conn:
        yield TicketStore(conn, year=26)


# --- rejection instead of a 500 --------------------------------------------


def test_no_contact_method_is_rejected_not_crashed(client) -> None:
    response = client.post("/intake", data=FILLED_FORM)

    assert response.status_code == 400


def test_rejection_creates_nothing(client, store) -> None:
    client.post("/intake", data=FILLED_FORM)

    assert store.list_tickets() == []


def test_rejection_does_not_burn_a_ref(client, store) -> None:
    """The next good intake should still be 26-0001."""
    client.post("/intake", data=FILLED_FORM)
    client.post("/intake", data={**FILLED_FORM, "phone": "555-0100"})

    assert store.list_tickets()[0].ref == "26-0001"


@pytest.mark.parametrize(
    ("field", "value"),
    [("customer_name", ""), ("customer_name", "   "), ("device", ""), ("device", "  ")],
)
def test_blank_required_fields_are_rejected(client, field, value) -> None:
    response = client.post("/intake", data={**FILLED_FORM, "phone": "555-0100", field: value})

    assert response.status_code == 400


# --- the error is visible ---------------------------------------------------


def test_error_message_is_shown(client) -> None:
    body = client.post("/intake", data=FILLED_FORM).text

    assert "at least one of phone or email" in body


def test_error_message_for_a_missing_device(client) -> None:
    body = client.post("/intake", data={**FILLED_FORM, "phone": "555", "device": ""}).text

    assert "device is required" in body


# --- nothing typed is lost --------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("customer_name", "DJ in LA"),
        ("device", "Pioneer CDJ-3000"),
        ("serial", "ABC123"),
        ("symptoms", "jog wheel unresponsive"),
        ("accessories", "no power brick"),
        ("condition_notes", "scuff on faceplate"),
    ],
)
def test_typed_values_come_back(client, field, value) -> None:
    body = client.post("/intake", data=FILLED_FORM).text

    assert value in body, f"{field} was lost on re-render"


def test_multiline_return_address_survives(client) -> None:
    """The most annoying field to re-type, and the one most likely to be long."""
    body = client.post("/intake", data=FILLED_FORM).text

    assert "1 Sunset Blvd" in body
    assert "Los Angeles, CA" in body


def test_values_are_escaped_on_re_render(client) -> None:
    """Preserved input is still untrusted input."""
    body = client.post(
        "/intake", data={**FILLED_FORM, "device": '<script>alert("x")</script>'}
    ).text

    assert "<script>alert(" not in body
    assert "&lt;script&gt;" in body


def test_the_form_is_still_a_working_form(client) -> None:
    body = client.post("/intake", data=FILLED_FORM).text

    assert 'action="/intake"' in body
    assert 'name="customer_name"' in body


# --- recovering -------------------------------------------------------------


def test_fixing_the_error_and_resubmitting_works(client, store) -> None:
    client.post("/intake", data=FILLED_FORM)

    response = client.post(
        "/intake", data={**FILLED_FORM, "phone": "555-0100"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert store.get_by_ref("26-0001").return_address == "1 Sunset Blvd\nLos Angeles, CA"


def test_a_valid_submission_is_unaffected(client) -> None:
    response = client.post(
        "/intake", data={**FILLED_FORM, "email": "dj@example.com"}, follow_redirects=False
    )

    assert response.status_code == 303
