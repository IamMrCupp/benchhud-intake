"""The intake form renders with the right fields and input types.

The input types aren't cosmetic — `type="tel"` and `type="email"` are what make
a tablet show the right keyboard, which is most of the "minimal typing" goal.
"""

import re

import pytest

from conftest import CsrfClient, sign_in
from intake.app import app

# Signed in: every route but /healthz and /login needs a session.
client = sign_in(CsrfClient(app))


@pytest.fixture
def html() -> str:
    return client.get("/intake").text


def _field(html: str, name: str) -> str:
    """The input/textarea tag for a named field."""
    match = re.search(rf'<(?:input|textarea)[^>]*\bname="{name}"[^>]*>', html)
    assert match, f"no field named {name!r} in the form"
    return match.group(0)


def test_form_page_renders() -> None:
    response = client.get("/intake")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_posts_to_intake(html) -> None:
    assert re.search(r'<form[^>]*method="post"', html, re.I)
    assert re.search(r'<form[^>]*action="/intake"', html, re.I)


@pytest.mark.parametrize(
    "name",
    [
        "customer_name",
        "phone",
        "email",
        "return_address",
        "device",
        "serial",
        "symptoms",
        "accessories",
        "condition_notes",
    ],
)
def test_every_field_is_present(html, name) -> None:
    _field(html, name)


def test_phone_uses_the_tel_keyboard(html) -> None:
    assert 'type="tel"' in _field(html, "phone")


def test_email_uses_the_email_keyboard(html) -> None:
    assert 'type="email"' in _field(html, "email")


def test_customer_name_is_autofocused(html) -> None:
    """It's the first thing you type with someone standing in front of you."""
    assert "autofocus" in _field(html, "customer_name")


@pytest.mark.parametrize("name", ["customer_name", "device"])
def test_required_fields_are_marked_required(html, name) -> None:
    assert "required" in _field(html, name)


@pytest.mark.parametrize("name", ["phone", "email", "serial", "accessories"])
def test_optional_fields_are_not_required(html, name) -> None:
    """Especially phone/email — either alone is enough, so neither can be
    required at the HTML level or the browser blocks a valid submission."""
    assert "required" not in _field(html, name)


@pytest.mark.parametrize("name", ["return_address", "symptoms", "condition_notes"])
def test_free_text_fields_are_textareas(html, name) -> None:
    assert _field(html, name).startswith("<textarea")


def test_return_address_does_not_nag(html) -> None:
    """Blank is the normal case for a walk-in — the field must not imply
    otherwise, or every walk-in intake looks incomplete."""
    assert "required" not in _field(html, "return_address")


def test_says_one_contact_method_is_needed(html) -> None:
    """HTML can't express 'at least one of these two', so the form has to say it
    in words — otherwise the rule only surfaces as a server-side rejection."""
    assert re.search(r"at least one", html, re.I)


def test_uses_the_base_layout(html) -> None:
    assert 'name="viewport"' in html
    assert "<main>" in html
