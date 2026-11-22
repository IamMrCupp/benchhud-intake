"""The Requests view: where a web request becomes a ticket, or doesn't.

These tests run the app against a database found through `INTAKE_DB_PATH`, with
no dependency overrides, so the relay's POST and the bench's browser are
exercised through the same wiring a deployment uses.
"""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import CsrfClient, sign_in
from intake import auth
from intake.app import SUBMIT_PATH, app
from intake.db import connect
from intake.schema import initialise
from intake.web_requests import NotPending, RequestStore

SUBMIT_TOKEN = "test-submit-token-not-a-real-one"
API_TOKEN = "test-api-token-not-a-real-one"

BODY = {
    "customer_name": "Renata Quist",
    "email": "renata-secret@example.com",
    "phone": "555-0142-SECRET",
    "device": "Pioneer CDJ-3000",
    "symptoms": "jog wheel unresponsive\nsince a drop",
    "handoff": "mail_in",
    "return_address": "14 Hidden Lane SECRET",
}


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(path))
    monkeypatch.setenv("INTAKE_UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, SUBMIT_TOKEN)
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, API_TOKEN)
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def client(db_path):
    return sign_in(CsrfClient(app))


@pytest.fixture
def relay(db_path):
    """The relay's side: a bare client that can only submit."""
    api = TestClient(app)

    def _submit(**overrides) -> str:
        response = api.post(
            SUBMIT_PATH,
            json={**BODY, **overrides},
            headers={"Authorization": f"Bearer {SUBMIT_TOKEN}"},
        )
        assert response.status_code == 202
        return response.json()["id"]

    return _submit


def _row(db_path, request_id) -> dict:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM requests WHERE public_id = ?", (request_id,)).fetchone()
        return dict(row)


def _api_tickets(db_path) -> list[dict]:
    return (
        TestClient(app).get("/api/tickets", headers={"Authorization": f"Bearer {API_TOKEN}"}).json()
    )


# --- the list ---------------------------------------------------------------


def test_an_empty_queue_says_so(client) -> None:
    body = client.get("/requests").text

    assert "No requests waiting." in body


def test_a_submitted_request_shows_up_in_the_list(client, relay) -> None:
    request_id = relay()

    body = client.get("/requests").text

    assert "Pioneer CDJ-3000" in body
    assert "Renata Quist" in body
    assert "mail-in" in body
    assert f'href="/requests/{request_id}"' in body


def test_the_list_shows_no_contact_details(client, relay) -> None:
    relay()

    body = client.get("/requests").text

    assert "SECRET" not in body and "renata-secret" not in body


def test_the_longest_waiting_request_is_first(client, relay) -> None:
    relay(device="First in")
    relay(device="Second in")

    body = client.get("/requests").text

    assert body.index("First in") < body.index("Second in")


def test_an_unknown_state_filter_is_a_400(client) -> None:
    assert client.get("/requests?state=everything").status_code == 400


# --- the count in the nav ---------------------------------------------------


def _count(html: str) -> str | None:
    match = re.search(r'<span class="count"[^>]*>(\d+)</span>', html)
    return match.group(1) if match else None


def test_no_count_when_nothing_is_waiting(client) -> None:
    assert _count(client.get("/tickets").text) is None
    assert 'href="/requests"' in client.get("/tickets").text


@pytest.mark.parametrize("page", ["/tickets", "/intake", "/requests"])
def test_the_pending_count_is_on_every_page(client, relay, page) -> None:
    relay()
    relay()

    assert _count(client.get(page).text) == "2"


def test_the_count_drops_as_requests_are_dealt_with(client, relay) -> None:
    first, second = relay(), relay()

    client.post(f"/requests/{first}/dismiss")
    assert _count(client.get("/tickets").text) == "1"
    client.post(f"/requests/{second}/accept")
    assert _count(client.get("/tickets").text) is None


def test_the_sign_in_page_never_shows_a_count(db_path, relay) -> None:
    """Signed out, the app says nothing about what's in the database."""
    relay()

    body = TestClient(app).get("/login").text

    assert _count(body) is None and 'href="/requests"' not in body


# --- the detail page --------------------------------------------------------


def test_the_detail_page_shows_everything_that_was_sent(client, relay) -> None:
    body = client.get(f"/requests/{relay()}").text

    for value in ("Renata Quist", "renata-secret@example.com", "555-0142-SECRET"):
        assert value in body
    assert "14 Hidden Lane SECRET" in body and "jog wheel unresponsive" in body
    assert "Accept" in body and "Dismiss" in body


def test_submitted_text_is_escaped_never_live(client, relay) -> None:
    """It was typed by a stranger on the public site."""
    request_id = relay(
        customer_name="<script>alert(1)</script>",
        symptoms='<img src=x onerror="alert(1)">',
        email="javascript:alert(1)",
    )

    for page in ("/requests", f"/requests/{request_id}"):
        body = client.get(page).text
        assert "<script>alert(1)" not in body and "<img src=x" not in body
        assert 'href="javascript:' not in body


def test_an_unknown_request_is_a_404(client) -> None:
    assert client.get("/requests/nope").status_code == 404
    assert client.post("/requests/nope/accept").status_code == 404
    assert client.post("/requests/nope/dismiss").status_code == 404


# --- accept -----------------------------------------------------------------


def test_accept_creates_a_ticket_with_the_fields_copied_over(client, relay) -> None:
    response = client.post(f"/requests/{relay()}/accept", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/" + response.headers["location"][-7:]
    ticket_page = client.get(response.headers["location"]).text
    for value in (
        "Pioneer CDJ-3000",
        "jog wheel unresponsive",
        "Renata Quist",
        "renata-secret@example.com",
        "555-0142-SECRET",
        "14 Hidden Lane SECRET",
    ):
        assert value in ticket_page


def test_the_ref_is_allocated_on_accept_and_not_before(client, relay, db_path) -> None:
    request_id = relay()
    relay()
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ref_counters").fetchone()[0] == 0

    location = client.post(f"/requests/{request_id}/accept", follow_redirects=False).headers[
        "location"
    ]

    with connect(db_path) as conn:
        assert conn.execute("SELECT last_counter FROM ref_counters").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert re.fullmatch(r"/tickets/\d\d-0001", location)


@pytest.mark.parametrize("handoff", ["mail_in", "drop_off"])
def test_an_accepted_ticket_starts_at_awaiting_arrival(client, relay, db_path, handoff) -> None:
    """Both ways in: saying yes doesn't put the device on the bench."""
    client.post(f"/requests/{relay(handoff=handoff)}/accept")

    (ticket,) = _api_tickets(db_path)
    assert ticket["status"] == "awaiting_arrival"


def test_a_pending_request_is_not_in_the_ticket_api_until_accepted(client, relay, db_path) -> None:
    request_id = relay()
    assert _api_tickets(db_path) == []

    client.post(f"/requests/{request_id}/accept")

    (ticket,) = _api_tickets(db_path)
    assert ticket["device"] == "Pioneer CDJ-3000"
    # And still only the first name crosses to benchhud.
    assert ticket["customer"] == "Renata"


def test_accept_leaves_a_stub_with_the_details_blanked(client, relay, db_path) -> None:
    request_id = relay()

    client.post(f"/requests/{request_id}/accept")

    row = _row(db_path, request_id)
    assert row["state"] == "accepted" and row["resolved_at"]
    assert (row["ticket_year"], row["ticket_counter"]) != (None, None)
    assert row["handoff"] == "mail_in"
    for column in ("customer_name", "phone", "email", "device", "symptoms", "return_address"):
        assert row[column] is None, column


def test_the_stub_page_points_at_the_ticket_and_offers_nothing(client, relay) -> None:
    request_id = relay()
    location = client.post(f"/requests/{request_id}/accept", follow_redirects=False).headers[
        "location"
    ]

    body = client.get(f"/requests/{request_id}").text
    listed = client.get("/requests?state=accepted").text

    assert f'href="{location}"' in body and f'href="{location}"' in listed
    assert "Renata" not in body and "/accept" not in body and "/dismiss" not in body


def test_accepting_twice_makes_one_ticket(client, relay, db_path) -> None:
    request_id = relay()
    client.post(f"/requests/{request_id}/accept")

    again = client.post(f"/requests/{request_id}/accept", follow_redirects=False)

    assert again.status_code == 409
    assert "already accepted" in again.text
    assert len(_api_tickets(db_path)) == 1


def test_two_accepts_at_once_make_one_ticket(client, relay, db_path) -> None:
    """A double tap, or two people at the bench. Both requests carry a valid
    session and token; only one may get a ticket."""
    from concurrent.futures import ThreadPoolExecutor

    for _ in range(5):
        request_id = relay()
        token = client._csrf()
        before = len(_api_tickets(db_path))

        def accept(request_id=request_id, token=token):
            return client.post(
                f"/requests/{request_id}/accept",
                data={"csrf_token": token},
                follow_redirects=False,
            ).status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = sorted(pool.map(lambda _: accept(), range(4)))

        assert codes == [303, 409, 409, 409]
        assert len(_api_tickets(db_path)) == before + 1


def test_a_failed_accept_puts_the_request_back(db_path, relay, monkeypatch) -> None:
    from intake.store import TicketStore

    request_id = relay()

    def boom(self, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(TicketStore, "create_ticket", boom)
    with connect(db_path) as conn, pytest.raises(RuntimeError):
        RequestStore(conn).accept(request_id, TicketStore(conn, year=26))

    row = _row(db_path, request_id)
    assert row["state"] == "pending" and row["resolved_at"] is None
    assert row["customer_name"] == "Renata Quist"


def test_the_ticket_is_never_seen_at_intake_on_its_way_in(db_path, relay) -> None:
    """One insert at awaiting_arrival, not an insert at intake and a move:
    a benchhud poll in between would have shown the wrong status."""
    from intake.store import TicketStore

    request_id = relay()
    with connect(db_path) as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        RequestStore(conn).accept(request_id, TicketStore(conn, year=26))

    assert not any("UPDATE tickets" in s for s in statements)


def test_the_count_comes_from_the_same_database_as_the_page(tmp_path, db_path, relay) -> None:
    """With the store overridden, the nav must not read some other file."""
    from intake.app import get_store
    from intake.store import TicketStore

    relay()  # one waiting in the INTAKE_DB_PATH database
    other = tmp_path / "other.db"

    def _store():
        with connect(other) as conn:
            initialise(conn)
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    try:
        page = sign_in(CsrfClient(app)).get("/requests").text
    finally:
        app.dependency_overrides.clear()

    assert _count(page) is None and "No requests waiting." in page


def test_a_late_relay_retry_after_accept_makes_no_new_request(client, db_path) -> None:
    api = TestClient(app)
    headers = {"Authorization": f"Bearer {SUBMIT_TOKEN}", "Idempotency-Key": "retry-1"}
    first = api.post(SUBMIT_PATH, json=BODY, headers=headers).json()["id"]
    client.post(f"/requests/{first}/accept")

    again = api.post(SUBMIT_PATH, json=BODY, headers=headers)

    assert (again.status_code, again.json()) == (202, {"id": first})
    assert "No requests waiting." in client.get("/requests").text


# --- dismiss ----------------------------------------------------------------


def test_dismiss_marks_it_and_makes_no_ticket(client, relay, db_path) -> None:
    request_id = relay()

    response = client.post(f"/requests/{request_id}/dismiss", follow_redirects=False)

    assert (response.status_code, response.headers["location"]) == (303, "/requests")
    row = _row(db_path, request_id)
    assert row["state"] == "dismissed" and row["resolved_at"]
    assert row["customer_name"] == "Renata Quist"  # kept until the retention window ends
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ref_counters").fetchone()[0] == 0


def test_a_dismissed_request_leaves_the_queue_but_can_still_be_read(client, relay) -> None:
    request_id = relay()
    client.post(f"/requests/{request_id}/dismiss")

    assert "No requests waiting." in client.get("/requests").text
    assert "Pioneer CDJ-3000" in client.get("/requests?state=dismissed").text
    detail = client.get(f"/requests/{request_id}").text
    assert "Renata Quist" in detail and "/accept" not in detail


def test_a_dismissed_request_cannot_be_accepted(client, relay, db_path) -> None:
    request_id = relay()
    client.post(f"/requests/{request_id}/dismiss")

    assert client.post(f"/requests/{request_id}/accept").status_code == 409
    assert _api_tickets(db_path) == []


def test_the_store_refuses_to_resolve_twice(db_path, relay) -> None:
    request_id = relay()
    with connect(db_path) as conn:
        store = RequestStore(conn)
        store.dismiss(request_id)
        with pytest.raises(NotPending):
            store.dismiss(request_id)
        with pytest.raises(LookupError):
            store.dismiss("nope")


# --- protected like every other form ----------------------------------------


@pytest.mark.parametrize("action", ["accept", "dismiss"])
def test_accept_and_dismiss_need_a_csrf_token(db_path, relay, action) -> None:
    request_id = relay()
    plain = sign_in(TestClient(app))  # signed in, but posts no token

    assert plain.post(f"/requests/{request_id}/{action}").status_code == 403
    wrong = plain.post(f"/requests/{request_id}/{action}", data={"csrf_token": "forged"})
    assert wrong.status_code == 403
    assert _row(db_path, request_id)["state"] == "pending"


def test_every_form_on_the_request_page_carries_a_token(client, relay) -> None:
    body = client.get(f"/requests/{relay()}").text

    forms = re.findall(r"<form[^>]*method=[\"']post[\"'][^>]*>.*?</form>", body, re.S | re.I)
    assert len(forms) >= 3  # accept, dismiss, and the sign-out in the header
    for form in forms:
        assert 'name="csrf_token"' in form


@pytest.mark.parametrize("path", ["/requests", "/requests/anything"])
def test_the_view_needs_a_session(db_path, relay, path) -> None:
    relay()

    response = TestClient(app).get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert "Renata" not in response.text


@pytest.mark.parametrize("action", ["accept", "dismiss"])
def test_signed_out_nobody_can_accept_or_dismiss(db_path, relay, action) -> None:
    request_id = relay()

    response = TestClient(app).post(f"/requests/{request_id}/{action}", follow_redirects=False)

    assert response.status_code == 303
    assert _row(db_path, request_id)["state"] == "pending"


@pytest.mark.parametrize("token", [SUBMIT_TOKEN, API_TOKEN])
def test_neither_token_opens_the_view(db_path, relay, token) -> None:
    request_id = relay()
    headers = {"Authorization": f"Bearer {token}"}
    bare = TestClient(app)

    for path in ("/requests", f"/requests/{request_id}"):
        assert bare.get(path, headers=headers, follow_redirects=False).status_code == 303
    bare.post(f"/requests/{request_id}/accept", headers=headers, follow_redirects=False)
    assert _row(db_path, request_id)["state"] == "pending"
