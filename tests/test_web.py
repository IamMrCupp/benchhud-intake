"""The server-rendered page path works end to end.

Phase 2's intake form depends on this, so these tests check the render path
itself — inheritance resolving, the viewport tag surviving — rather than the
placeholder copy, which is expected to change.
"""

from fastapi.testclient import TestClient

from conftest import sign_in
from intake.app import app

# Signed in: every route but /healthz and /login needs a session.
client = sign_in(TestClient(app))


def test_index_renders_html() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_index_inherits_from_base_layout() -> None:
    """Content from the child template and chrome from the parent both appear."""
    body = client.get("/").text

    assert "<!doctype html>" in body.lower()
    assert "<main>" in body
    assert "Phase 2" in body


def test_viewport_meta_is_present() -> None:
    """Without this, a tablet renders at desktop width and the form is unusable."""
    body = client.get("/").text

    assert 'name="viewport"' in body
    assert "width=device-width" in body
