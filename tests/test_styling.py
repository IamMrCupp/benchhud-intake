"""The tablet rules that are actually checkable.

Whether the app is *pleasant* to use standing at a bench with a customer waiting
is acceptance test 8 and needs a real tablet and a real person. These tests
cover only the mechanical rules — the ones that are easy to break later without
noticing, and that have concrete right answers.
"""

import re

import pytest
from fastapi.testclient import TestClient

from conftest import sign_in
from intake.app import app, get_store
from intake.db import connect
from intake.schema import initialise
from intake.store import TicketStore


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
    # Auth gates everything but /healthz and /login, so a client testing page
    # content has to sign in. The gate itself is tested in test_auth_required.
    yield sign_in(TestClient(app))
    app.dependency_overrides.clear()


@pytest.fixture
def css(client) -> str:
    body = client.get("/intake").text
    match = re.search(r"<style>(.*?)</style>", body, re.S)
    assert match, "no stylesheet in the base layout"
    return match.group(1)


def test_styles_are_inline(css) -> None:
    """No build step and no framework — plan.md non-goals."""
    assert len(css) > 500


def test_no_external_stylesheets(client) -> None:
    """A LAN-only app must not depend on a CDN it can't reach with the WAN down."""
    body = client.get("/intake").text

    assert '<link rel="stylesheet"' not in body
    assert "cdn." not in body


def test_touch_target_floor_is_44px(css) -> None:
    """The long-standing minimum for a reliable finger target."""
    assert re.search(r"--touch:\s*44px", css)


def test_inputs_use_the_touch_minimum(css) -> None:
    assert re.search(r"min-height:\s*var\(--touch\)", css)


def test_no_input_font_below_16px(css) -> None:
    """Below 16px, iOS Safari zooms the viewport on focus and the layout jumps
    mid-typing. It's a hard floor, not a preference."""
    sizes = [int(n) for n in re.findall(r"font-size:\s*(\d+)px", css)]

    assert sizes, "expected explicit pixel font sizes"
    assert min(sizes) >= 16, f"font size below the iOS zoom threshold: {min(sizes)}px"


def test_the_ref_is_dramatically_larger_than_body_text(css) -> None:
    """It gets read across a bench and copied onto tape."""
    match = re.search(r"\.ref-display\s*\{[^}]*font-size:\s*clamp\(([^,]+),", css)

    assert match, "confirmation ref has no explicit size"
    assert float(match.group(1).replace("rem", "").strip()) >= 3


def test_return_address_preserves_line_breaks(css) -> None:
    """A multi-line address must render as typed — it goes on a shipping label."""
    assert re.search(r"dd\.address\s*\{[^}]*white-space:\s*pre-line", css)


def test_layout_is_width_constrained(css) -> None:
    """Full-bleed text on a tablet is unreadable."""
    assert "max-width" in css


def test_dark_mode_is_handled(css) -> None:
    """The bench is not always brightly lit, and tablets follow the system."""
    assert "prefers-color-scheme: dark" in css


@pytest.mark.parametrize("path", ["/", "/intake", "/tickets"])
def test_every_page_inherits_the_styles(client, path) -> None:
    assert "--touch" in client.get(path).text
