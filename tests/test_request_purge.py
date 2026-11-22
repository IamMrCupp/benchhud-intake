"""Retention: dealt-with requests don't sit here forever.

A dismissed request is the name and contact details of someone who never
became a customer. These tests pin when it goes, what never goes, and that it
happens without anyone having to remember to run something.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import CsrfClient, sign_in
from intake import auth, web_requests
from intake.app import SUBMIT_PATH, app
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore
from intake.web_requests import RequestStore, validate

SUBMIT_TOKEN = "test-submit-token-not-a-real-one"
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=UTC)

BODY = {
    "customer_name": "Renata Quist",
    "email": "renata@example.com",
    "device": "Pioneer CDJ-3000",
    "symptoms": "jog wheel unresponsive",
    "handoff": "drop_off",
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(path))
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, SUBMIT_TOKEN)
    monkeypatch.delenv(web_requests.RETENTION_ENV_VAR, raising=False)
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def conn(db_path):
    with connect(db_path) as c:
        yield c


@pytest.fixture
def store(conn):
    return RequestStore(conn)


def _add(store, key=None) -> str:
    return store.submit(validate(BODY), idempotency_key=key)[0]


def _age(conn, public_id, days) -> None:
    """Pretend a request was dealt with `days` before NOW."""
    stamp = (NOW - timedelta(days=days)).isoformat()
    with conn:
        conn.execute("UPDATE requests SET resolved_at = ? WHERE public_id = ?", (stamp, public_id))


def _ids(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT public_id FROM requests")}


# --- the store --------------------------------------------------------------


def test_a_dismissed_request_is_deleted_after_the_window(store, conn) -> None:
    old, recent = _add(store), _add(store)
    store.dismiss(old)
    store.dismiss(recent)
    _age(conn, old, 31)
    _age(conn, recent, 29)

    assert store.purge(now=NOW) == 1
    assert _ids(conn) == {recent}


def test_the_window_is_30_days_by_default(store, conn) -> None:
    assert web_requests.retention_days() == 30
    request_id = _add(store)
    store.dismiss(request_id)

    _age(conn, request_id, 30)
    assert store.purge(now=NOW - timedelta(seconds=1)) == 0
    assert store.purge(now=NOW + timedelta(seconds=1)) == 1


def test_a_purged_request_takes_its_details_with_it(store, conn) -> None:
    request_id = _add(store)
    store.dismiss(request_id)
    _age(conn, request_id, 31)

    store.purge(now=NOW)

    dump = "\n".join(conn.iterdump())
    assert "Renata" not in dump and "renata@example.com" not in dump


def test_a_pending_request_is_never_purged_however_old(store, conn) -> None:
    request_id = _add(store)
    with conn:
        conn.execute("UPDATE requests SET received_at = '2020-01-01T00:00:00+00:00'")

    assert store.purge(now=NOW) == 0
    assert _ids(conn) == {request_id}


def test_an_accepted_stub_is_purged_too_and_the_ticket_stays(store, conn) -> None:
    request_id = _add(store)
    ticket = store.accept(request_id, TicketStore(conn, year=26))
    _age(conn, request_id, 31)

    assert store.purge(now=NOW) == 1
    assert _ids(conn) == set()
    kept = TicketStore(conn, year=26).get_by_ref(ticket.ref)
    assert kept is not None and kept.customer_name == "Renata Quist"


def test_a_recent_stub_is_kept(store, conn) -> None:
    request_id = _add(store)
    store.accept(request_id, TicketStore(conn, year=26))
    _age(conn, request_id, 5)

    assert store.purge(now=NOW) == 0


def test_the_window_is_configurable(store, conn, monkeypatch) -> None:
    request_id = _add(store)
    store.dismiss(request_id)
    _age(conn, request_id, 8)
    assert store.purge(now=NOW) == 0

    monkeypatch.setenv(web_requests.RETENTION_ENV_VAR, "7")

    assert store.purge(now=NOW) == 1


@pytest.mark.parametrize("raw", ["", "soon", "-1", "1.5"])
def test_a_nonsense_window_falls_back_to_30_days_not_to_forever(monkeypatch, raw) -> None:
    monkeypatch.setenv(web_requests.RETENTION_ENV_VAR, raw)

    assert web_requests.retention_days() == web_requests.DEFAULT_RETENTION_DAYS


def test_a_purged_key_no_longer_dedupes(store, conn) -> None:
    """The idempotency key lives as long as its row. A retry a month late is
    a new request, which is the honest answer by then."""
    first = _add(store, key="k")
    store.dismiss(first)
    _age(conn, first, 31)
    store.purge(now=NOW)

    assert _add(store, key="k") != first


# --- it happens without being asked -----------------------------------------


def _expire_one(db_path) -> str:
    with connect(db_path) as conn:
        store = RequestStore(conn)
        request_id = _add(store)
        store.dismiss(request_id)
        with conn:
            conn.execute("UPDATE requests SET resolved_at = '2020-01-01T00:00:00+00:00'")
    return request_id


@pytest.mark.parametrize("page", ["/tickets", "/requests", "/intake"])
def test_loading_any_page_signed_in_purges(db_path, page) -> None:
    _expire_one(db_path)

    assert sign_in(CsrfClient(app)).get(page).status_code == 200

    with connect(db_path) as conn:
        assert _ids(conn) == set()


def test_a_new_submission_purges(db_path) -> None:
    expired = _expire_one(db_path)

    response = TestClient(app).post(
        SUBMIT_PATH, json=BODY, headers={"Authorization": f"Bearer {SUBMIT_TOKEN}"}
    )

    with connect(db_path) as conn:
        assert _ids(conn) == {response.json()["id"]}
        assert expired not in _ids(conn)


def test_a_signed_out_page_load_touches_nothing(db_path) -> None:
    expired = _expire_one(db_path)

    TestClient(app).get("/login")
    TestClient(app).get("/healthz")

    with connect(db_path) as conn:
        assert _ids(conn) == {expired}


def test_the_dismissed_list_says_when_they_go(db_path) -> None:
    with connect(db_path) as conn:
        store = RequestStore(conn)
        request_id = _add(store)
        store.dismiss(request_id)
    client = sign_in(CsrfClient(app))

    assert "deleted 30 days" in " ".join(client.get("/requests?state=dismissed").text.split())
    assert "30 days after it was dismissed" in " ".join(
        client.get(f"/requests/{request_id}").text.split()
    )
