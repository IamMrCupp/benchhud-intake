"""The board-reference UI: board_id on the form, links on the ticket page.

The one security-shaped test is `test_a_javascript_url_does_not_execute` — links
are user-supplied and rendered into a page, which is exactly where a
`javascript:` href would be a stored-XSS foothold.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import CsrfClient, sign_in
from intake.app import app, get_photo_store, get_store
from intake.db import connect
from intake.photos import PhotoStore
from intake.schema import initialise
from intake.store import TicketStore


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
def client(db_path, tmp_path):
    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    app.dependency_overrides[get_photo_store] = lambda: PhotoStore(tmp_path / "uploads")
    yield sign_in(CsrfClient(app))
    app.dependency_overrides.clear()


def _intake(**extra):
    return {
        "customer_name": "DJ in LA",
        "device": "CDJ-3000",
        "phone": "555-0100",
        "email": "",
        "return_address": "",
        "serial": "",
        "symptoms": "",
        "accessories": "",
        "condition_notes": "",
        "board_id": "",
        **extra,
    }


# --- intake form ------------------------------------------------------------


def test_intake_form_has_a_board_id_field(client) -> None:
    assert 'name="board_id"' in client.get("/intake").text


def test_board_id_is_optional_at_intake(client, store) -> None:
    """No board number yet is the common case — it mustn't block intake."""
    response = client.post("/intake", data=_intake(), follow_redirects=False)

    assert response.status_code == 303
    assert store.get_by_ref("26-0001").board_id is None


def test_board_id_entered_at_intake_is_stored(client, store) -> None:
    client.post("/intake", data=_intake(board_id="820-00239"))

    assert store.get_by_ref("26-0001").board_id == "820-00239"


# --- ticket detail: board_id ------------------------------------------------


def test_detail_shows_board_id(client, store) -> None:
    client.post("/intake", data=_intake(board_id="820-00239"))

    assert "820-00239" in client.get("/tickets/26-0001").text


def test_detail_has_an_editable_board_id(client) -> None:
    client.post("/intake", data=_intake())

    assert 'name="board_id"' in client.get("/tickets/26-0001").text


def test_board_id_can_be_set_from_the_detail_page(client, store) -> None:
    client.post("/intake", data=_intake())

    client.post("/tickets/26-0001", data={"board_id": "820-00239"})

    assert store.get_by_ref("26-0001").board_id == "820-00239"


# --- ticket detail: links ---------------------------------------------------


def test_a_link_can_be_added(client, store) -> None:
    client.post("/intake", data=_intake())

    client.post(
        "/tickets/26-0001/links",
        data={"label": "Boardview", "url": "http://nas/820-00239.obv"},
    )

    links = store.list_links("26-0001")
    assert len(links) == 1
    assert links[0].url == "http://nas/820-00239.obv"


def test_added_link_renders_clickable(client) -> None:
    client.post("/intake", data=_intake())
    client.post(
        "/tickets/26-0001/links", data={"label": "Boardview", "url": "http://nas/a.obv"}
    )

    body = client.get("/tickets/26-0001").text
    assert 'href="http://nas/a.obv"' in body
    assert "Boardview" in body
    assert 'rel="noopener' in body


def test_a_link_can_be_removed(client, store) -> None:
    client.post("/intake", data=_intake())
    client.post("/tickets/26-0001/links", data={"label": "A", "url": "http://nas/a.obv"})
    link_id = store.list_links("26-0001")[0].id

    client.post(f"/tickets/26-0001/links/{link_id}/delete", data={})

    assert store.list_links("26-0001") == []


def test_adding_a_link_redirects_back(client) -> None:
    client.post("/intake", data=_intake())

    response = client.post(
        "/tickets/26-0001/links",
        data={"label": "A", "url": "http://nas/a.obv"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/26-0001"


def test_an_empty_url_is_rejected(client, store) -> None:
    client.post("/intake", data=_intake())

    response = client.post("/tickets/26-0001/links", data={"label": "A", "url": ""})

    assert response.status_code == 400
    assert store.list_links("26-0001") == []


def test_links_on_an_unknown_ticket_404(client) -> None:
    response = client.post(
        "/tickets/26-9999/links",
        data={"label": "A", "url": "http://nas/a.obv"},
        follow_redirects=False,
    )

    assert response.status_code == 404


# --- safety -----------------------------------------------------------------


def test_a_javascript_url_does_not_execute(client) -> None:
    """User-supplied hrefs rendered into a page are a stored-XSS surface."""
    client.post("/intake", data=_intake())
    client.post(
        "/tickets/26-0001/links",
        data={"label": "<script>x</script>", "url": "javascript:alert(1)"},
    )

    body = client.get("/tickets/26-0001").text
    assert "<script>x</script>" not in body
    assert "&lt;script&gt;" in body
    # A javascript: href must not be emitted as a live link.
    assert 'href="javascript:' not in body


def test_the_new_forms_carry_a_csrf_token(client) -> None:
    client.post("/intake", data=_intake())
    body = client.get("/tickets/26-0001").text

    import re

    for form in re.findall(r"<form[^>]*action=\"/tickets/26-0001/links[^>]*>.*?</form>", body, re.S):
        assert 'name="csrf_token"' in form
