"""`POST /api/requests`: the write-only door for the public site's relay.

The contract's shapes are covered by test_request_contract.py. This module is
about the promises around them: what the submit token can and cannot reach,
that a request is not a ticket, and the two backstops (the pending cap and the
idempotency key).
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in
from intake import auth, web_requests
from intake.app import SUBMIT_PATH, _client_ip, app
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore
from intake.web_requests import RequestStore

SUBMIT_TOKEN = "test-submit-token-not-a-real-one"
API_TOKEN = "test-api-token-not-a-real-one"

BODY = {
    "customer_name": "Renata Quist",
    "email": "renata-secret@example.com",
    "phone": "555-0142-SECRET",
    "device": "Pioneer CDJ-3000",
    "symptoms": "jog wheel unresponsive SECRET",
    "handoff": "mail_in",
    "return_address": "14 Hidden Lane SECRET",
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(path))
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, SUBMIT_TOKEN)
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, API_TOKEN)
    monkeypatch.delenv(web_requests.MAX_PENDING_ENV_VAR, raising=False)
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def client(db_path):
    return TestClient(app)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _submit(client, body=BODY, token=SUBMIT_TOKEN, **headers):
    return client.post(SUBMIT_PATH, json=body, headers={**_bearer(token), **headers})


def _requests(db_path):
    with connect(db_path) as conn:
        return RequestStore(conn).list_requests()


def _sql(db_path, query):
    with connect(db_path) as conn:
        return conn.execute(query).fetchall()


# --- it stores a pending request --------------------------------------------


def test_a_valid_submission_is_stored_as_pending(client, db_path) -> None:
    response = _submit(client)

    assert response.status_code == 202
    (stored,) = _requests(db_path)
    assert stored.id == response.json()["id"]
    assert stored.state == "pending"
    assert stored.customer_name == "Renata Quist"
    assert stored.email == "renata-secret@example.com"
    assert stored.phone == "555-0142-SECRET"
    assert stored.device == "Pioneer CDJ-3000"
    assert stored.symptoms == "jog wheel unresponsive SECRET"
    assert stored.handoff == "mail_in"
    assert stored.return_address == "14 Hidden Lane SECRET"
    assert stored.received_at and stored.resolved_at is None and stored.ticket_ref is None


def test_values_are_cleaned_the_way_tickets_are(client, db_path) -> None:
    _submit(client, {**BODY, "customer_name": "  Renata Quist \n", "phone": "   "})

    (stored,) = _requests(db_path)
    assert stored.customer_name == "Renata Quist"
    assert stored.phone is None


def test_the_response_echoes_nothing_that_was_sent(client) -> None:
    response = _submit(client)

    assert set(response.json()) == {"id"}
    for value in BODY.values():
        assert value not in response.text


def test_the_id_is_not_the_row_number(client) -> None:
    """Opaque: a relay (or anyone reading its logs) learns no count from it."""
    ids = [_submit(client).json()["id"] for _ in range(3)]

    assert len(set(ids)) == 3
    assert not any(i.isdigit() for i in ids)


def test_a_validation_error_names_fields_never_values(client, db_path) -> None:
    response = _submit(client, {**BODY, "handoff": "SECRET-courier", "device": "S" * 500})

    assert response.status_code == 400
    assert response.json()["errors"] == [
        {"field": "device", "code": "too_long"},
        {"field": "handoff", "code": "invalid"},
    ]
    assert "SECRET" not in response.text and "SSSS" not in response.text
    assert _requests(db_path) == []


def test_an_unknown_key_is_not_named_back(client) -> None:
    response = _submit(client, {**BODY, "SECRET-key": "x"})

    assert response.status_code == 400
    assert "SECRET" not in response.text


def test_bad_utf8_is_a_400_not_a_500(client) -> None:
    response = client.post(
        SUBMIT_PATH,
        content=b'{"customer_name": "\xff\xfe"}',
        headers={**_bearer(SUBMIT_TOKEN), "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["errors"] == [{"field": "body", "code": "invalid_json"}]


# --- who gets in ------------------------------------------------------------


def test_no_token_is_401(client, db_path) -> None:
    response = client.post(SUBMIT_PATH, json=BODY)

    assert response.status_code == 401
    assert _requests(db_path) == []


@pytest.mark.parametrize(
    "header",
    ["Bearer wrong", f"Basic {SUBMIT_TOKEN}", SUBMIT_TOKEN, "Bearer ", "Bearer"],
)
def test_a_wrong_or_malformed_header_is_401(client, header) -> None:
    response = client.post(SUBMIT_PATH, json=BODY, headers={"Authorization": header})

    assert response.status_code == 401


def test_it_fails_closed_when_no_submit_token_is_configured(client, monkeypatch, db_path) -> None:
    monkeypatch.delenv(auth.SUBMIT_TOKEN_ENV_VAR)

    assert _submit(client).status_code == 401
    assert client.post(SUBMIT_PATH, json=BODY, headers=_bearer("")).status_code == 401
    assert _requests(db_path) == []


def test_a_blank_configured_token_admits_nobody(client, monkeypatch) -> None:
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, "   ")

    assert _submit(client, token="   ").status_code == 401
    assert _submit(client, token="").status_code == 401


def test_the_api_token_cannot_submit(client, db_path) -> None:
    """benchhud's token reads the queue. It has no business writing requests."""
    assert _submit(client, token=API_TOKEN).status_code == 401
    assert _requests(db_path) == []


def test_a_browser_session_cannot_submit(client, db_path) -> None:
    """A signed-in browser posting JSON here would skip the CSRF check, so the
    session opens nothing on this route."""
    sign_in(client)

    assert client.post(SUBMIT_PATH, json=BODY).status_code == 401
    assert _requests(db_path) == []


def test_one_value_for_both_tokens_disables_the_submit_token(client, monkeypatch) -> None:
    """Otherwise the relay's token would read the ticket API."""
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, SUBMIT_TOKEN)

    assert _submit(client).status_code == 401


# --- the submit token reads nothing -----------------------------------------


@pytest.fixture
def ticket(db_path):
    with connect(db_path) as conn:
        return TicketStore(conn, year=26).create_ticket(
            device="ThinkPad X1", customer_name="Family", phone="555-0100"
        )


def test_the_submit_token_gets_401_on_the_ticket_list(client, ticket) -> None:
    assert client.get("/api/tickets", headers=_bearer(API_TOKEN)).status_code == 200

    response = client.get("/api/tickets", headers=_bearer(SUBMIT_TOKEN))

    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_the_submit_token_gets_401_on_one_ticket(client, ticket) -> None:
    assert client.get(f"/api/tickets/{ticket.ref}", headers=_bearer(API_TOKEN)).status_code == 200

    response = client.get(f"/api/tickets/{ticket.ref}", headers=_bearer(SUBMIT_TOKEN))

    assert response.status_code == 401
    assert ticket.ref not in response.text


@pytest.mark.parametrize("path", ["/api/requests/", "/api/requests/x", "/api/anything"])
def test_the_submit_token_opens_only_the_exact_path(client, path) -> None:
    for method in ("get", "post"):
        assert getattr(client, method)(path, headers=_bearer(SUBMIT_TOKEN)).status_code == 401


@pytest.mark.parametrize("path", ["/tickets", "/intake", "/tickets/26-0001"])
def test_the_submit_token_opens_no_browser_page(client, ticket, path) -> None:
    response = client.get(path, headers=_bearer(SUBMIT_TOKEN), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_requests_cannot_be_read_back_over_the_api(client) -> None:
    """Write-only: there is no GET, for either token."""
    request_id = _submit(client).json()["id"]

    assert client.get(SUBMIT_PATH, headers=_bearer(SUBMIT_TOKEN)).status_code == 405
    assert client.get(SUBMIT_PATH, headers=_bearer(API_TOKEN)).status_code == 401
    for token in (SUBMIT_TOKEN, API_TOKEN):
        assert client.get(f"{SUBMIT_PATH}/{request_id}", headers=_bearer(token)).status_code in (
            401,
            404,
        )


# --- a request is not a ticket ----------------------------------------------


def test_a_pending_request_never_appears_in_the_ticket_api(client, ticket) -> None:
    _submit(client)

    listed = client.get("/api/tickets", headers=_bearer(API_TOKEN))
    everything = client.get("/api/tickets?status=intake", headers=_bearer(API_TOKEN))

    assert [t["ref"] for t in listed.json()] == [ticket.ref]
    for response in (listed, everything):
        assert "Pioneer" not in response.text and "Renata" not in response.text


def test_a_submission_allocates_no_ref(client, db_path) -> None:
    for _ in range(3):
        assert _submit(client).status_code == 202

    assert _sql(db_path, "SELECT * FROM ref_counters") == []
    assert _sql(db_path, "SELECT * FROM tickets") == []
    # And the next real ticket is still the first of the year.
    with connect(db_path) as conn:
        made = TicketStore(conn, year=26).create_ticket(
            device="Board", customer_name="Walk-in", phone="555-0100"
        )
    assert made.ref == "26-0001"


def test_a_rejected_submission_allocates_no_ref_either(client, db_path) -> None:
    _submit(client, {**BODY, "device": ""})

    assert _sql(db_path, "SELECT * FROM ref_counters") == []


# --- the pending cap --------------------------------------------------------


def test_the_cap_returns_429_and_stores_nothing_more(client, db_path, monkeypatch) -> None:
    monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, "2")

    assert [_submit(client).status_code for _ in range(3)] == [202, 202, 429]
    assert len(_requests(db_path)) == 2


def test_the_cap_counts_only_pending_requests(client, db_path, monkeypatch) -> None:
    monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, "1")
    _submit(client)
    assert _submit(client).status_code == 429

    with connect(db_path) as conn, conn:
        conn.execute("UPDATE requests SET state = 'dismissed', resolved_at = '2026-11-22T00:00:00'")

    assert _submit(client).status_code == 202


def test_the_default_cap_is_200(monkeypatch) -> None:
    monkeypatch.delenv(web_requests.MAX_PENDING_ENV_VAR, raising=False)

    assert web_requests.max_pending() == 200


@pytest.mark.parametrize("raw", ["", "  ", "lots", "-1", "2.5"])
def test_a_nonsense_cap_falls_back_to_the_default_not_to_no_cap(monkeypatch, raw) -> None:
    monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, raw)

    assert web_requests.max_pending() == web_requests.DEFAULT_MAX_PENDING


# --- the idempotency key ----------------------------------------------------


def test_a_retry_with_the_same_key_makes_one_request(client, db_path) -> None:
    first = _submit(client, **{"Idempotency-Key": "retry-1"})
    second = _submit(client, **{"Idempotency-Key": "retry-1"})

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(_requests(db_path)) == 1


def test_different_keys_and_no_key_make_separate_requests(client, db_path) -> None:
    ids = {
        _submit(client, **{"Idempotency-Key": "a"}).json()["id"],
        _submit(client, **{"Idempotency-Key": "b"}).json()["id"],
        _submit(client).json()["id"],
        _submit(client).json()["id"],
    }

    assert len(ids) == 4
    assert len(_requests(db_path)) == 4


def test_a_retry_still_succeeds_after_the_queue_fills(client, monkeypatch) -> None:
    """The first attempt was stored. Telling the retry "full" would be a lie."""
    monkeypatch.setenv(web_requests.MAX_PENDING_ENV_VAR, "1")
    first = _submit(client, **{"Idempotency-Key": "retry-1"})

    assert _submit(client, **{"Idempotency-Key": "other"}).status_code == 429
    again = _submit(client, **{"Idempotency-Key": "retry-1"})
    assert (again.status_code, again.json()) == (202, first.json())


def test_a_retry_does_not_overwrite_the_first_body(client, db_path) -> None:
    _submit(client, **{"Idempotency-Key": "retry-1"})
    _submit(client, {**BODY, "device": "Something else"}, **{"Idempotency-Key": "retry-1"})

    (stored,) = _requests(db_path)
    assert stored.device == "Pioneer CDJ-3000"


def test_a_key_does_not_rescue_an_invalid_body(client, db_path) -> None:
    response = _submit(client, {**BODY, "device": ""}, **{"Idempotency-Key": "retry-1"})

    assert response.status_code == 400
    assert _requests(db_path) == []


# --- the client address -----------------------------------------------------


def test_client_ip_still_ignores_x_forwarded_for() -> None:
    """The relay sits in front of this route now, and that changes nothing:
    per-IP limiting is the relay's job, and a header anyone can set must not
    choose a throttle bucket here."""
    request = SimpleNamespace(
        client=SimpleNamespace(host="10.0.0.7"),
        headers={"x-forwarded-for": "203.0.113.9"},
    )

    assert _client_ip(request) == "10.0.0.7"


def test_a_forwarded_header_changes_nothing_about_a_submission(client, db_path) -> None:
    response = _submit(client, **{"X-Forwarded-For": "203.0.113.9"})

    assert response.status_code == 202
    assert "203.0.113.9" not in str(tuple(_sql(db_path, "SELECT * FROM requests")[0]))
