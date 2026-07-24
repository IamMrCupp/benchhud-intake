"""The read-only ticket API benchhud polls.

Two things get exercised hard here: the auth (a machine client uses a bearer
token, not the browser session) and the promise that no PII reaches the wire.
The projection is unit-tested in test_projection.py; here we confirm it actually
holds end-to-end through a real HTTP response.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import CsrfClient, sign_in
from intake.app import app, get_photo_store, get_store
from intake.db import connect
from intake.photos import PhotoStore
from intake.schema import initialise
from intake.store import TicketStore

TOKEN = "test-api-token-not-a-real-one"


@pytest.fixture(autouse=True)
def _configure_token(monkeypatch):
    monkeypatch.setenv("INTAKE_API_TOKEN", TOKEN)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def store(db_path):
    with connect(db_path) as conn:
        yield TicketStore(conn, year=26)


@pytest.fixture
def api(db_path, tmp_path):
    """A bare client — no session. The API is reached with a bearer token."""

    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    app.dependency_overrides[get_photo_store] = lambda: PhotoStore(tmp_path / "uploads")
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(api, store):
    """One open ticket (with full PII) and one closed."""
    store.create_ticket(
        device="Pioneer CDJ-3000",
        symptoms="jog wheel unresponsive",
        customer_name="Renata Quist",
        phone="555-0142-SECRET",
        email="renata-secret@example.com",
        return_address="14 Hidden Lane SECRET",
        serial="SN-SECRET",
        accessories="charger SECRET",
        condition_notes="scuff SECRET",
    )
    store.set_status("26-0001", "diagnosing")
    store.create_ticket(device="ThinkPad X1", customer_name="Family", phone="555-0100")
    store.set_status("26-0002", "closed", outcome="repaired")
    return api


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


# --- auth -------------------------------------------------------------------


def test_no_credentials_is_401(api) -> None:
    assert api.get("/api/tickets").status_code == 401


def test_a_wrong_token_is_401(api) -> None:
    assert api.get("/api/tickets", headers=_auth("nope")).status_code == 401


def test_a_valid_token_is_200(seeded) -> None:
    assert seeded.get("/api/tickets", headers=_auth()).status_code == 200


def test_failure_is_json_not_a_login_redirect(api) -> None:
    """A machine client wants a status code, not an HTML login page."""
    response = api.get("/api/tickets", follow_redirects=False)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")


def test_a_session_also_grants_access(db_path, tmp_path, monkeypatch) -> None:
    """A logged-in human can curl the API for debugging."""
    monkeypatch.setenv("INTAKE_API_TOKEN", TOKEN)

    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    app.dependency_overrides[get_photo_store] = lambda: PhotoStore(tmp_path / "uploads")
    try:
        browser = sign_in(CsrfClient(app))
        assert browser.get("/api/tickets").status_code == 200  # no bearer header
    finally:
        app.dependency_overrides.clear()


def test_a_missing_token_config_fails_closed(seeded, monkeypatch) -> None:
    """With no INTAKE_API_TOKEN set, bearer auth must reject everything —
    never 'no token configured means any token works'."""
    monkeypatch.delenv("INTAKE_API_TOKEN", raising=False)

    assert seeded.get("/api/tickets", headers=_auth("anything")).status_code == 401


# --- listing ----------------------------------------------------------------


def test_open_tickets_are_listed(seeded) -> None:
    body = seeded.get("/api/tickets", headers=_auth()).json()

    refs = [t["ref"] for t in body]
    assert "26-0001" in refs
    assert "26-0002" not in refs  # closed


def test_default_is_open(seeded) -> None:
    assert (
        seeded.get("/api/tickets", headers=_auth()).json()
        == seeded.get("/api/tickets?status=open", headers=_auth()).json()
    )


def test_a_specific_status_filters(seeded) -> None:
    body = seeded.get("/api/tickets?status=closed", headers=_auth()).json()

    assert [t["ref"] for t in body] == ["26-0002"]


def test_an_unknown_status_is_400(seeded) -> None:
    assert seeded.get("/api/tickets?status=diagnosin", headers=_auth()).status_code == 400


def test_list_items_have_the_contract_shape(seeded) -> None:
    ticket = seeded.get("/api/tickets", headers=_auth()).json()[0]

    assert set(ticket) == {"ref", "device", "symptoms", "customer", "status", "url"}


# --- single ticket ----------------------------------------------------------


def test_one_ticket_by_ref(seeded) -> None:
    ticket = seeded.get("/api/tickets/26-0001", headers=_auth()).json()

    assert ticket["ref"] == "26-0001"
    assert ticket["customer"] == "Renata Quist"
    assert ticket["status"] == "diagnosing"


def test_unknown_ref_is_404(seeded) -> None:
    assert seeded.get("/api/tickets/26-9999", headers=_auth()).status_code == 404


def test_malformed_ref_is_404(seeded) -> None:
    assert seeded.get("/api/tickets/nonsense", headers=_auth()).status_code == 404


def test_the_deep_link_is_present(seeded) -> None:
    ticket = seeded.get("/api/tickets/26-0001", headers=_auth()).json()

    assert ticket["url"].endswith("/tickets/26-0001")


# --- the privacy guarantee, end to end --------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "555-0142-SECRET",
        "renata-secret@example.com",
        "14 Hidden Lane SECRET",
        "SN-SECRET",
        "charger SECRET",
        "scuff SECRET",
    ],
)
def test_no_pii_reaches_the_wire(seeded, secret) -> None:
    """The projection promises this; confirm it survives all the way through a
    real HTTP response, list and detail both."""
    listing = seeded.get("/api/tickets", headers=_auth()).text
    detail = seeded.get("/api/tickets/26-0001", headers=_auth()).text

    assert secret not in listing
    assert secret not in detail


def test_only_the_name_appears(seeded) -> None:
    ticket = seeded.get("/api/tickets/26-0001", headers=_auth()).json()

    assert ticket["customer"] == "Renata Quist"
    assert "@" not in seeded.get("/api/tickets/26-0001", headers=_auth()).text.replace(
        ticket["url"], ""
    )
