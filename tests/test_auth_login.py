"""Login, logout, and the session cookie.

Two tests here are about not leaking information rather than about function:
`test_wrong_password_message_is_unhelpful` and the open-redirect guard. Both are
cheap now and awkward to retrofit once someone relies on the current behaviour.
"""

import pytest
from fastapi.testclient import TestClient

from intake.app import app, get_store
from intake.auth import hash_password
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore

PASSWORD = "bench password"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("INTAKE_PASSWORD_HASH", hash_password(PASSWORD))
    monkeypatch.setenv("INTAKE_SESSION_SECRET", "test-secret-not-a-real-one")


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


def _login(client, password=PASSWORD, **extra):
    return client.post("/login", data={"password": password, **extra}, follow_redirects=False)


# --- the login page ---------------------------------------------------------


def test_login_page_renders(client) -> None:
    response = client.get("/login")

    assert response.status_code == 200
    assert 'name="password"' in response.text


def test_login_page_uses_a_password_field(client) -> None:
    """Not `type="text"` — shoulder-surfing at a bench is the whole point."""
    assert 'type="password"' in client.get("/login").text


def test_login_page_is_reachable_without_a_session(client) -> None:
    assert client.get("/login").status_code == 200


# --- logging in -------------------------------------------------------------


def test_correct_password_redirects(client) -> None:
    response = _login(client)

    assert response.status_code == 303


def test_correct_password_sets_a_session_cookie(client) -> None:
    response = _login(client)

    assert "session" in response.cookies or "session" in response.headers.get("set-cookie", "")


def test_session_persists_across_requests(client) -> None:
    _login(client)

    assert client.get("/tickets", follow_redirects=False).status_code == 200


def test_wrong_password_is_rejected(client) -> None:
    response = _login(client, password="not the password")

    assert response.status_code == 401


def test_wrong_password_grants_no_session(client) -> None:
    _login(client, password="not the password")

    assert client.get("/tickets", follow_redirects=False).status_code != 200


def test_empty_password_is_rejected(client) -> None:
    assert _login(client, password="").status_code == 401


def test_wrong_password_message_is_unhelpful(client) -> None:
    """Must not distinguish 'wrong password' from 'none configured', and must
    not hint at length or format."""
    body = _login(client, password="wrong").text.lower()

    for leak in ["not set", "not configured", "too short", "length", "hash"]:
        assert leak not in body


# --- logging out ------------------------------------------------------------


def test_logout_ends_the_session(client) -> None:
    _login(client)
    client.post("/logout", follow_redirects=False)

    assert client.get("/tickets", follow_redirects=False).status_code != 200


def test_logout_redirects_to_login(client) -> None:
    _login(client)
    response = client.post("/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


# --- ?next= -----------------------------------------------------------------


def test_next_is_honoured(client) -> None:
    """A deep link into a ticket must survive the login round-trip."""
    response = _login(client, next="/tickets/26-0001")

    assert response.headers["location"] == "/tickets/26-0001"


def test_login_defaults_to_the_ticket_list(client) -> None:
    assert _login(client).headers["location"] == "/tickets"


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example.com/phish",
        "//evil.example.com",
        "http://evil.example.com",
        "javascript:alert(1)",
        "\\\\evil.example.com",
    ],
)
def test_offsite_next_is_ignored(client, hostile) -> None:
    """Open-redirect guard: a login link is exactly what gets pasted into a
    message, and bouncing to an attacker's page after a real login is how
    credentials get phished."""
    location = _login(client, next=hostile).headers["location"]

    assert location == "/tickets", f"open redirect via {hostile!r}"


# --- cookie flags -----------------------------------------------------------


def test_cookie_is_httponly(client) -> None:
    header = _login(client).headers.get("set-cookie", "")

    assert "httponly" in header.lower()


def test_cookie_is_samesite_lax(client) -> None:
    header = _login(client).headers.get("set-cookie", "").lower()

    assert "samesite=lax" in header


def test_cookie_is_not_secure_by_default(client) -> None:
    """LAN-only over plain HTTP by design. A `secure` cookie would never be
    sent, so login would appear to work and then silently fail on every
    following request — a confusing failure to debug."""
    header = _login(client).headers.get("set-cookie", "").lower()

    assert "secure" not in header


def test_cookie_can_be_made_secure(client, monkeypatch) -> None:
    """For when a TLS terminator lands in front of it."""
    monkeypatch.setenv("INTAKE_COOKIE_SECURE", "1")

    from intake import auth

    assert auth.cookie_secure() is True
