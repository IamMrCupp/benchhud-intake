"""Uploading photos, at intake and afterwards.

The partial-failure case is the one with a real decision behind it: a bad photo
must not cost someone the intake they just typed. The ticket is the record that
matters; a rejected photo is recoverable (retake it), a lost intake is not.
"""

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from conftest import CsrfClient, sign_in
from intake.app import app, get_photo_store, get_store
from intake.db import connect
from intake.photos import PhotoStore
from intake.schema import initialise
from intake.store import TicketStore


def _img(fmt="JPEG", colour=(80, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), colour).save(buf, format=fmt)
    return buf.getvalue()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
    return path


@pytest.fixture
def photo_store(tmp_path):
    return PhotoStore(tmp_path / "uploads")


@pytest.fixture
def client(db_path, photo_store):
    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    app.dependency_overrides[get_photo_store] = lambda: photo_store
    yield sign_in(CsrfClient(app))
    app.dependency_overrides.clear()


def _intake(**extra):
    return {
        "customer_name": "DJ in LA",
        "device": "Pioneer CDJ-3000",
        "phone": "555-0100",
        "email": "",
        "return_address": "",
        "serial": "",
        "symptoms": "",
        "accessories": "",
        "condition_notes": "",
        **extra,
    }


# --- the form ---------------------------------------------------------------


def test_intake_form_has_a_camera_input(client) -> None:
    body = client.get("/intake").text

    assert 'type="file"' in body
    assert 'capture="environment"' in body  # opens the camera on a tablet
    assert "multiple" in body
    assert 'accept="image/' in body


def test_ticket_page_has_an_upload_form(client) -> None:
    client.post("/intake", data=_intake())
    body = client.get("/tickets/26-0001").text

    assert 'action="/tickets/26-0001/photos"' in body
    assert 'type="file"' in body


# --- uploading at intake ----------------------------------------------------


def test_a_photo_at_intake_attaches_to_the_ticket(client, photo_store) -> None:
    client.post("/intake", data=_intake(), files=[("photos", ("a.jpg", _img(), "image/jpeg"))])

    assert len(photo_store.list_photos("26-0001")) == 1


def test_multiple_photos_at_intake(client, photo_store) -> None:
    client.post(
        "/intake",
        data=_intake(),
        files=[
            ("photos", ("a.jpg", _img(), "image/jpeg")),
            ("photos", ("b.png", _img(fmt="PNG"), "image/png")),
            ("photos", ("c.jpg", _img(), "image/jpeg")),
        ],
    )

    assert len(photo_store.list_photos("26-0001")) == 3


def test_intake_with_no_photo_still_works(client, photo_store) -> None:
    response = client.post("/intake", data=_intake(), follow_redirects=False)

    assert response.status_code == 303
    assert photo_store.list_photos("26-0001") == []


def test_an_empty_file_field_is_ignored(client, photo_store) -> None:
    """A browser submits an empty file part when nothing is chosen."""
    client.post("/intake", data=_intake(), files=[("photos", ("", b"", "application/octet-stream"))])

    assert photo_store.list_photos("26-0001") == []


# --- partial failure --------------------------------------------------------


def test_a_bad_photo_does_not_block_the_ticket(client, db_path) -> None:
    """The decision: the intake succeeds even if a photo is rejected."""
    client.post(
        "/intake",
        data=_intake(),
        files=[("photos", ("evil.jpg", b"not an image", "image/jpeg"))],
        follow_redirects=False,
    )

    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_good_photos_survive_a_bad_one(client, photo_store) -> None:
    client.post(
        "/intake",
        data=_intake(),
        files=[
            ("photos", ("good.jpg", _img(), "image/jpeg")),
            ("photos", ("bad.jpg", b"garbage", "image/jpeg")),
        ],
    )

    assert len(photo_store.list_photos("26-0001")) == 1


def test_the_failure_is_surfaced(client) -> None:
    location = client.post(
        "/intake",
        data=_intake(),
        files=[("photos", ("bad.jpg", b"garbage", "image/jpeg"))],
        follow_redirects=False,
    ).headers["location"]

    assert "photos_failed=1" in location
    assert "did not upload" in client.get(location).text.lower()


# --- uploading after intake -------------------------------------------------


def test_a_photo_can_be_added_later(client, photo_store) -> None:
    client.post("/intake", data=_intake())

    response = client.post(
        "/tickets/26-0001/photos",
        files=[("photos", ("bench.jpg", _img(), "image/jpeg"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/26-0001"
    assert len(photo_store.list_photos("26-0001")) == 1


def test_later_upload_of_a_bad_photo_is_reported(client, photo_store) -> None:
    client.post("/intake", data=_intake())

    location = client.post(
        "/tickets/26-0001/photos",
        files=[("photos", ("bad.jpg", b"nope", "image/jpeg"))],
        follow_redirects=False,
    ).headers["location"]

    assert "photos_failed=1" in location
    assert photo_store.list_photos("26-0001") == []


def test_upload_to_an_unknown_ticket_404s(client) -> None:
    response = client.post(
        "/tickets/26-9999/photos",
        files=[("photos", ("a.jpg", _img(), "image/jpeg"))],
        follow_redirects=False,
    )

    assert response.status_code == 404


# --- protection -------------------------------------------------------------


def test_upload_needs_a_session(db_path, photo_store) -> None:
    app.dependency_overrides[get_store] = lambda: None
    app.dependency_overrides[get_photo_store] = lambda: photo_store
    try:
        signed_out = TestClient(app)
        response = signed_out.post(
            "/tickets/26-0001/photos",
            files=[("photos", ("a.jpg", _img(), "image/jpeg"))],
            follow_redirects=False,
        )
        # Either the auth redirect or the CSRF refusal — never a success.
        assert response.status_code in (303, 403)
        assert response.status_code != 200
    finally:
        app.dependency_overrides.clear()
