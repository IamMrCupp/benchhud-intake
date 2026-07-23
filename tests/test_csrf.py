"""CSRF protection on mutating routes.

`test_every_form_in_the_app_carries_a_token` is the durable one: it crawls the
app's own pages and checks each `<form method=post>` rather than asserting
against a list someone has to maintain. Phase 3 adds a photo upload form, and
this should fail if it ships without a token.
"""

import re

import pytest
from fastapi.testclient import TestClient

from intake.app import app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore

from conftest import TEST_PASSWORD, sign_in


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
    yield sign_in(TestClient(app))
    app.dependency_overrides.clear()


def _token(client, path="/intake") -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', client.get(path).text)
    assert match, f"no csrf token on {path}"
    return match.group(1)


def _intake_data(token=None, **extra):
    data = {
        "customer_name": "DJ in LA",
        "device": "Pioneer CDJ-3000",
        "phone": "555-0100",
        "email": "",
        "return_address": "",
        "serial": "",
        "symptoms": "",
        "accessories": "",
        "condition_notes": "",
        **extra,
    }
    if token is not None:
        data["csrf_token"] = token
    return data


# --- refusal ----------------------------------------------------------------


def test_post_without_a_token_is_refused(client) -> None:
    response = client.post("/intake", data=_intake_data(), follow_redirects=False)

    assert response.status_code == 403


def test_post_with_a_garbage_token_is_refused(client) -> None:
    response = client.post(
        "/intake", data=_intake_data(token="not-a-real-token"), follow_redirects=False
    )

    assert response.status_code == 403


def test_post_with_a_truncated_token_is_refused(client) -> None:
    good = _token(client)

    response = client.post(
        "/intake", data=_intake_data(token=good[:-4]), follow_redirects=False
    )

    assert response.status_code == 403


def test_refused_post_writes_nothing(client, db_path) -> None:
    """The point of the whole exercise: a forged request must not create a
    ticket, not merely be reported as failed."""
    client.post("/intake", data=_intake_data(), follow_redirects=False)

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_status_change_without_a_token_is_refused(client, db_path) -> None:
    client.post("/intake", data=_intake_data(token=_token(client)))

    response = client.post(
        "/tickets/26-0001/status", data={"status": "closed", "outcome": "repaired"}
    )

    assert response.status_code == 403


def test_a_token_from_another_session_is_refused(client, db_path) -> None:
    """Tokens are bound to a session, so a stolen one from elsewhere is useless."""

    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    other = sign_in(TestClient(app))
    foreign = _token(other)

    response = client.post(
        "/intake", data=_intake_data(token=foreign), follow_redirects=False
    )

    assert response.status_code == 403


# --- acceptance -------------------------------------------------------------


def test_post_with_a_valid_token_works(client) -> None:
    response = client.post(
        "/intake", data=_intake_data(token=_token(client)), follow_redirects=False
    )

    assert response.status_code == 303


def test_the_whole_flow_still_works_with_tokens(client) -> None:
    client.post("/intake", data=_intake_data(token=_token(client)))

    status_token = _token(client, "/tickets/26-0001")
    response = client.post(
        "/tickets/26-0001/status",
        data={"status": "closed", "outcome": "repaired", "csrf_token": status_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "closed" in client.get("/tickets/26-0001").text


def test_login_carries_a_token(client) -> None:
    """An attacker logging a victim in is a real attack shape, and exempting
    login would mean the rule has an exception to remember."""
    assert 'name="csrf_token"' in client.get("/login").text


def test_logout_carries_a_token(client) -> None:
    assert 'name="csrf_token"' in client.get("/tickets").text


# --- the durable guard ------------------------------------------------------


@pytest.mark.parametrize("page", ["/intake", "/tickets", "/login"])
def test_every_form_in_the_app_carries_a_token(client, page) -> None:
    """Crawl rendered pages rather than checking against a maintained list.

    Phase 3 adds a photo upload form; this should fail if it ships without a
    token, without anyone having to remember to extend a checklist.
    """
    body = client.get(page).text

    for form in re.findall(r"<form[^>]*method=[\"']post[\"'][^>]*>.*?</form>", body, re.S | re.I):
        action = re.search(r'action="([^"]*)"', form)
        assert 'name="csrf_token"' in form, (
            f"form action={action.group(1) if action else '?'} on {page} has no CSRF token"
        )


def test_ticket_detail_forms_carry_a_token(client) -> None:
    client.post("/intake", data=_intake_data(token=_token(client)))
    body = client.get("/tickets/26-0001").text

    forms = re.findall(r"<form[^>]*method=[\"']post[\"'][^>]*>.*?</form>", body, re.S | re.I)
    assert forms, "expected at least one form on the ticket detail page"
    for form in forms:
        assert 'name="csrf_token"' in form


def test_get_requests_need_no_token(client) -> None:
    """Reads are unaffected — a token requirement on GET would break links."""
    for path in ["/", "/intake", "/tickets", "/login"]:
        assert client.get(path).status_code == 200


def test_password_is_still_required_alongside_csrf(client) -> None:
    """CSRF is not authentication; a valid token must not substitute for one."""
    signed_out = TestClient(app)

    response = signed_out.post(
        "/intake", data=_intake_data(token="anything"), follow_redirects=False
    )

    assert response.status_code in (303, 403)
    assert "/login" in response.headers.get("location", "/login")
