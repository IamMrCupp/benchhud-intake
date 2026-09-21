"""The server-rendered page path works end to end.

Phase 2's intake form depends on this, so these tests check the render path
itself — inheritance resolving, the viewport tag surviving — rather than the
placeholder copy, which is expected to change.
"""

from conftest import CsrfClient, sign_in
from intake.app import app

# Signed in: every route but /healthz and /login needs a session.
client = sign_in(CsrfClient(app))


def test_index_redirects_to_the_ticket_list() -> None:
    """The app's home is the ticket list — same place login's `next` falls back
    to. The Phase 0 placeholder page survived two months past the form it said
    didn't exist, and the first real sign-in on the deployed cluster saw it."""
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets"


def test_viewport_meta_is_present() -> None:
    """Without this, a tablet renders at desktop width and the form is unusable."""
    body = client.get("/").text

    assert 'name="viewport"' in body
    assert "width=device-width" in body
