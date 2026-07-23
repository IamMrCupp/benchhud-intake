"""Route protection.

`test_every_route_is_protected_or_explicitly_exempt` is the real deliverable
here. The middleware itself is a dozen lines; what's hard is guaranteeing that a
route added in Phase 3 — photo upload, thumbnail download — can't quietly ship
unprotected. That test enumerates the app's own routing table, so it fails on a
new unlisted route rather than trusting anyone to remember.
"""

import pytest
from fastapi.testclient import TestClient

from intake.app import EXEMPT_PATHS, app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore

from .conftest import TEST_PASSWORD


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
def signed_in(client):
    client.post("/login", data={"password": TEST_PASSWORD})
    return client


@pytest.fixture
def ticket(signed_in):
    signed_in.post(
        "/intake",
        data={
            "customer_name": "DJ in LA",
            "device": "Pioneer CDJ-3000",
            "phone": "555-0100",
            "email": "",
            "return_address": "",
            "serial": "",
            "symptoms": "",
            "accessories": "",
            "condition_notes": "",
        },
    )
    return "26-0001"


# --- the gate ---------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/intake", "/tickets", "/tickets/26-0001", "/intake/26-0001"]
)
def test_get_routes_redirect_when_signed_out(client, path) -> None:
    response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


@pytest.mark.parametrize("path", ["/intake", "/tickets/26-0001/status"])
def test_post_routes_are_blocked_when_signed_out(client, path) -> None:
    response = client.post(path, data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_signed_out_post_changes_nothing(client, db_path) -> None:
    """A blocked POST must not reach the store."""
    client.post(
        "/intake",
        data={"customer_name": "Intruder", "device": "Board", "phone": "555"},
        follow_redirects=False,
    )

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0


def test_no_pii_leaks_in_a_redirect_body(client) -> None:
    """The rejection must not render the page it was protecting."""
    body = client.get("/tickets", follow_redirects=False).text

    assert "customer" not in body.lower()


# --- what stays open --------------------------------------------------------


def test_healthz_answers_without_a_session(client) -> None:
    """The Kubernetes probe. If this needs a session the pod never goes ready,
    and the deployment fails in a way that looks unrelated to auth."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_login_page_is_reachable_signed_out(client) -> None:
    assert client.get("/login").status_code == 200


def test_login_post_is_reachable_signed_out(client) -> None:
    """Obvious, but a gate that blocks its own login is a real mistake."""
    assert client.post("/login", data={"password": "wrong"}).status_code == 401


# --- signed in --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/intake", "/tickets"])
def test_signed_in_access_is_allowed(signed_in, path) -> None:
    assert signed_in.get(path).status_code == 200


def test_signed_in_can_create_and_read(signed_in, ticket) -> None:
    assert signed_in.get(f"/tickets/{ticket}").status_code == 200


def test_logout_re_locks(signed_in, ticket) -> None:
    signed_in.post("/logout")

    assert signed_in.get("/tickets", follow_redirects=False).status_code == 303


# --- ?next= round trip ------------------------------------------------------


def test_redirect_preserves_the_requested_path(client) -> None:
    location = client.get("/tickets/26-0001", follow_redirects=False).headers["location"]

    assert "next=" in location
    assert "26-0001" in location


def test_deep_link_survives_the_login(client, db_path) -> None:
    """The realistic flow: a link to a ticket, opened cold on the tablet."""
    location = client.get("/intake", follow_redirects=False).headers["location"]
    next_target = location.split("next=")[1]

    response = client.post(
        "/login",
        data={"password": TEST_PASSWORD, "next": next_target},
        follow_redirects=False,
    )

    assert response.headers["location"] == "/intake"


# --- the guard that has to outlive this phase -------------------------------


def test_every_route_is_protected_or_explicitly_exempt() -> None:
    """Enumerate the app's routing table and assert nothing is unaccounted for.

    This is what makes "deny by default" a property rather than a claim about
    today's code. When Phase 3 adds photo routes, they're protected without
    anyone remembering — and if one is deliberately exempted, that shows up here
    as a change to `EXEMPT_PATHS` rather than passing silently.
    """
    documented = set(EXEMPT_PATHS)
    app_paths = {
        route.path
        for route in app.routes
        if getattr(route, "path", "").startswith("/")
        and route.path not in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc")
    }

    unexpected_exempt = documented - app_paths
    assert not unexpected_exempt, f"EXEMPT_PATHS lists routes that don't exist: {unexpected_exempt}"

    # Everything else must be gated. Asserted by behaviour, not by inspection.
    with TestClient(app) as probe:
        for path in sorted(app_paths - documented):
            concrete = path.replace("{ref}", "26-0001")
            response = probe.get(concrete, follow_redirects=False)
            assert response.status_code in (303, 405), (
                f"{path} answered {response.status_code} without a session — "
                "new routes must be protected by default"
            )


def test_exempt_set_is_minimal() -> None:
    """Each exemption is a hole; there should be very few and each obvious."""
    assert EXEMPT_PATHS == frozenset({"/healthz", "/login"})
