"""Behind a reverse proxy: whose address counts.

Failed logins are counted per client address. Behind a proxy every browser
arrives from the proxy, so without help one person's wrong guesses lock out the
whole bench. `INTAKE_TRUSTED_PROXIES` names the proxies whose
`X-Forwarded-For` is believed; `serve` and the tray app hand the list to the server
(`tests/test_cli.py` covers that half).

These tests put the app behind the same middleware the server installs, with
the same list, and connect from addresses inside and outside it.
"""

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from conftest import CsrfClient, sign_in
from intake import auth, proxies
from intake.app import SUBMIT_PATH, app
from intake.db import connect
from intake.ratelimit import DEFAULT_MAX_ATTEMPTS
from intake.schema import initialise

#: The ingress controller's pod range, and one pod in it.
PROXY_RANGE = "10.42.0.0/16"
PROXY = ("10.42.1.2", 50000)
#: Something else on the network that is not the proxy.
STRANGER = ("192.168.7.99", 50000)

TABLET = "192.168.7.50"
LAPTOP = "192.168.7.51"

API_TOKEN = "test-api-token-not-a-real-one"
SUBMIT_TOKEN = "test-submit-token-not-a-real-one"


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB_PATH", str(path))
    monkeypatch.setenv("INTAKE_UPLOADS_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv(auth.API_TOKEN_ENV_VAR, API_TOKEN)
    monkeypatch.setenv(auth.SUBMIT_TOKEN_ENV_VAR, SUBMIT_TOKEN)
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def behind_proxy(db_path, monkeypatch):
    """The app as `serve` runs it with the setting on: the list goes through
    the same parser, into the same middleware."""
    monkeypatch.setenv(proxies.TRUSTED_PROXIES_ENV_VAR, PROXY_RANGE)
    return ProxyHeadersMiddleware(app, trusted_hosts=proxies.trusted_proxies())


def _client(asgi_app, peer) -> CsrfClient:
    return CsrfClient(asgi_app, client=peer)


def _wrong_password(client, forwarded_for: str | None = None) -> int:
    headers = {"X-Forwarded-For": forwarded_for} if forwarded_for else {}
    response = client.post(
        "/login", data={"password": "not the password"}, headers=headers, follow_redirects=False
    )
    return response.status_code


def _lock_out(client, forwarded_for: str | None = None) -> None:
    for _ in range(DEFAULT_MAX_ATTEMPTS):
        assert _wrong_password(client, forwarded_for) == 401
    assert _wrong_password(client, forwarded_for) == 429


# --- the bug ----------------------------------------------------------------


def test_without_the_setting_everyone_behind_a_proxy_shares_one_lockout(db_path) -> None:
    """What issue #154 is about, pinned so the fix below is measured against it.
    The header is ignored, so the tablet's wrong guesses lock the laptop out."""
    client = _client(app, PROXY)

    _lock_out(client, TABLET)

    assert _wrong_password(client, LAPTOP) == 429


# --- the fix ----------------------------------------------------------------


def test_one_browser_locked_out_does_not_lock_another(behind_proxy) -> None:
    client = _client(behind_proxy, PROXY)

    _lock_out(client, TABLET)

    assert _wrong_password(client, LAPTOP) == 401


def test_the_locked_out_browser_stays_locked_out(behind_proxy) -> None:
    client = _client(behind_proxy, PROXY)

    _lock_out(client, TABLET)
    _wrong_password(client, LAPTOP)

    assert _wrong_password(client, TABLET) == 429


def test_a_header_from_anyone_but_the_proxy_is_ignored(behind_proxy) -> None:
    """The point of the whole design. Someone on the LAN who talks to the app
    directly cannot buy a fresh bucket by changing a header."""
    client = _client(behind_proxy, STRANGER)

    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        assert _wrong_password(client, f"203.0.113.{attempt}") == 401

    assert _wrong_password(client, "203.0.113.200") == 429


def test_a_header_the_client_forged_ahead_of_the_proxy_is_not_believed(behind_proxy) -> None:
    """A proxy appends the address it saw. Whatever the browser put in the
    header first sits to the left of that and is never the one used."""
    client = _client(behind_proxy, PROXY)

    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        assert _wrong_password(client, f"203.0.113.{attempt}, {TABLET}") == 401

    assert _wrong_password(client, f"203.0.113.200, {TABLET}") == 429


def test_the_proxy_without_a_header_is_its_own_bucket(behind_proxy) -> None:
    client = _client(behind_proxy, PROXY)

    _lock_out(client)

    assert _wrong_password(client, TABLET) == 401


# --- the scheme -------------------------------------------------------------


def _one_ticket_url(client, **headers) -> str:
    response = client.get(
        "/api/tickets", headers={"Authorization": f"Bearer {API_TOKEN}", **headers}
    )
    return response.json()[0]["url"]


@pytest.fixture
def a_ticket(behind_proxy):
    """One ticket, made the long way round so nothing here reaches into the store."""
    client = sign_in(_client(behind_proxy, PROXY))
    submitted = TestClient(behind_proxy, client=PROXY).post(
        SUBMIT_PATH,
        json={
            "customer_name": "Renata Quist",
            "phone": "555-0142",
            "device": "Pioneer CDJ-3000",
            "symptoms": "jog wheel unresponsive",
            "handoff": "drop_off",
        },
        headers={"Authorization": f"Bearer {SUBMIT_TOKEN}"},
    )
    assert submitted.status_code == 202
    accepted = client.post(f"/requests/{submitted.json()['id']}/accept", follow_redirects=False)
    assert accepted.status_code == 303


def test_links_say_https_when_the_proxy_says_so(behind_proxy, a_ticket) -> None:
    url = _one_ticket_url(_client(behind_proxy, PROXY), **{"X-Forwarded-Proto": "https"})

    assert url.startswith("https://")


def test_the_scheme_is_not_taken_from_a_stranger(behind_proxy, a_ticket) -> None:
    url = _one_ticket_url(_client(behind_proxy, STRANGER), **{"X-Forwarded-Proto": "https"})

    assert url.startswith("http://")


# --- the real server, over real HTTP ----------------------------------------
#
# Everything above goes through the middleware by hand. These start the server
# the way the tray app does and connect over loopback, which is the address the
# server would believe by default if nothing told it otherwise.


async def _who_am_i(scope, receive, send) -> None:
    """The smallest app that can answer "what address do you think I am?"."""
    if scope["type"] != "http":
        return
    client = scope.get("client") or ("", 0)
    body = f"{scope['scheme']} {client[0]}".encode()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def _ask(forwarded_for: str) -> str:
    import urllib.request

    from intake.server import ServerController

    controller = ServerController(app_factory=lambda: _who_am_i)
    state = controller.start("127.0.0.1", 0, timeout=5)
    assert state.running, state.error
    try:
        request = urllib.request.Request(
            state.url,
            headers={"X-Forwarded-For": forwarded_for, "X-Forwarded-Proto": "https"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.read().decode()
    finally:
        controller.stop()


def test_unset_the_real_server_believes_nobody_not_even_loopback(monkeypatch) -> None:
    """On a one-machine install every browser is loopback. If loopback were
    believed, any of them could choose its own lockout bucket."""
    monkeypatch.delenv(proxies.TRUSTED_PROXIES_ENV_VAR, raising=False)

    assert _ask(TABLET) == "http 127.0.0.1"


def test_set_the_real_server_believes_the_listed_proxy(monkeypatch) -> None:
    monkeypatch.setenv(proxies.TRUSTED_PROXIES_ENV_VAR, "127.0.0.1")

    assert _ask(TABLET) == f"https {TABLET}"


def test_listing_some_other_proxy_does_not_make_loopback_believed(monkeypatch) -> None:
    monkeypatch.setenv(proxies.TRUSTED_PROXIES_ENV_VAR, PROXY_RANGE)

    assert _ask(TABLET) == "http 127.0.0.1"


def test_the_tray_server_will_not_start_on_a_list_it_cannot_read(monkeypatch) -> None:
    from intake.server import ServerController

    monkeypatch.setenv(proxies.TRUSTED_PROXIES_ENV_VAR, "*")

    state = ServerController(app_factory=lambda: _who_am_i).start("127.0.0.1", 0, timeout=5)

    assert not state.running
    assert proxies.TRUSTED_PROXIES_ENV_VAR in state.error
    assert "every address" in state.error
