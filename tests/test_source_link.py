"""The footer's Source link.

AGPL section 13 obliges whoever runs a modified copy as a network service to
offer its users the source. The stock app carries the offer so a fork inherits
it, which only works if three things stay true: the link is on every page, it's
there *before* sign-in, and a fork can point it at its own code.

The last test is the privacy one. A footer renders on every page, including the
ones that show a customer — so it must hold the link and the licence name and
nothing else.
"""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import CsrfClient, sign_in
from intake.app import DEFAULT_SOURCE_URL, LICENSE_NAME, SOURCE_URL_ENV_VAR, app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore

FORK_URL = "https://git.example.invalid/someone/intake-fork"


@pytest.fixture(autouse=True)
def _upstream_default(monkeypatch):
    """Start every test from the stock configuration, whatever the shell has set."""
    monkeypatch.delenv(SOURCE_URL_ENV_VAR, raising=False)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def anonymous(db_path):
    """No session — what someone sees before they've signed in."""

    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def client(anonymous, db_path):
    return sign_in(CsrfClient(app))


@pytest.fixture
def ticket(db_path):
    with connect(db_path) as conn:
        return TicketStore(conn, year=26).create_ticket(
            device="Pioneer CDJ-3000",
            serial="ABC123",
            symptoms="jog wheel unresponsive",
            customer_name="DJ in LA",
            phone="555-0100",
            email="dj@example.com",
            return_address="1 Sunset Blvd\nLos Angeles, CA",
        )


def _footer(body: str) -> str:
    match = re.search(r"<footer>(.*?)</footer>", body, re.S)
    assert match, "the base layout has no footer"
    return match.group(1)


def _link(url: str) -> str:
    return f'<a href="{url}" target="_blank" rel="noopener noreferrer">Source</a>'


def test_sign_in_page_offers_the_source_without_a_session(anonymous) -> None:
    response = anonymous.get("/login")

    assert response.status_code == 200
    footer = _footer(response.text)
    assert _link(DEFAULT_SOURCE_URL) in footer
    assert LICENSE_NAME in footer


def test_default_points_at_upstream() -> None:
    assert DEFAULT_SOURCE_URL == "https://github.com/IamMrCupp/benchhud-intake"
    assert LICENSE_NAME == "AGPL-3.0-or-later"


def test_every_page_carries_the_link(client, ticket) -> None:
    for path in ("/login", "/intake", "/tickets", f"/tickets/{ticket.ref}"):
        assert _link(DEFAULT_SOURCE_URL) in _footer(client.get(path).text), path


def test_a_fork_can_point_the_link_at_its_own_source(anonymous, monkeypatch) -> None:
    monkeypatch.setenv(SOURCE_URL_ENV_VAR, FORK_URL)

    body = anonymous.get("/login").text

    assert _link(FORK_URL) in _footer(body)
    assert DEFAULT_SOURCE_URL not in body


@pytest.mark.parametrize("value", ["", "   ", "javascript:alert(1)", "example.invalid/fork"])
def test_a_value_that_is_not_a_web_address_falls_back_to_upstream(
    anonymous, monkeypatch, value
) -> None:
    """It lands in an ``href``. A typo must not leave a dead or scripted link."""
    monkeypatch.setenv(SOURCE_URL_ENV_VAR, value)

    assert _link(DEFAULT_SOURCE_URL) in _footer(anonymous.get("/login").text)


def test_footer_holds_the_link_and_the_licence_and_nothing_else(client, ticket) -> None:
    """On the page that shows the most about a customer, the footer still says
    only where the code is."""
    footer = _footer(client.get(f"/tickets/{ticket.ref}").text)

    text = " ".join(re.sub(r"<[^>]+>", " ", footer).split())
    assert text == f"Source · {LICENSE_NAME}"
    assert _link(DEFAULT_SOURCE_URL) in footer
