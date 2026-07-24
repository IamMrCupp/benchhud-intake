"""Serving photos back.

`test_photos_are_not_reachable_signed_out` is the one that matters: these are
pictures of customers' property, and the whole reason they're served through the
app rather than a static mount is so the auth middleware covers them. A static
`uploads/` mount would have put them back on the open LAN.
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


def _img() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 700), (60, 90, 60)).save(buf, format="JPEG")
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


@pytest.fixture
def photo_name(client, photo_store):
    client.post(
        "/intake",
        data={
            "customer_name": "DJ in LA",
            "device": "CDJ-3000",
            "phone": "555-0100",
            "email": "",
            "return_address": "",
            "serial": "",
            "symptoms": "",
            "accessories": "",
            "condition_notes": "",
        },
        files=[("photos", ("a.jpg", _img(), "image/jpeg"))],
    )
    return photo_store.list_photos("26-0001")[0].name


# --- serving ----------------------------------------------------------------


def test_full_image_is_served(client, photo_name) -> None:
    response = client.get(f"/tickets/26-0001/photos/{photo_name}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert len(response.content) > 0


def test_thumbnail_is_served(client, photo_name) -> None:
    response = client.get(f"/tickets/26-0001/photos/{photo_name}/thumb")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"


def test_thumbnail_is_smaller_than_the_full_image(client, photo_name) -> None:
    full = client.get(f"/tickets/26-0001/photos/{photo_name}").content
    thumb = client.get(f"/tickets/26-0001/photos/{photo_name}/thumb").content

    assert len(thumb) < len(full)


def test_the_served_bytes_are_a_real_image(client, photo_name) -> None:
    response = client.get(f"/tickets/26-0001/photos/{photo_name}")

    with Image.open(io.BytesIO(response.content)) as img:
        assert img.format == "JPEG"


def test_the_gallery_links_resolve(client, photo_name) -> None:
    """The thumbnail src and full-image href on the page must actually serve."""
    page = client.get("/tickets/26-0001").text

    assert f"/tickets/26-0001/photos/{photo_name}" in page
    assert client.get(f"/tickets/26-0001/photos/{photo_name}/thumb").status_code == 200


# --- missing ----------------------------------------------------------------


def test_a_missing_photo_404s(client) -> None:
    assert client.get("/tickets/26-0001/photos/deadbeef.jpg").status_code == 404


def test_a_photo_on_an_unknown_ticket_404s(client) -> None:
    assert client.get("/tickets/26-9999/photos/x.jpg").status_code == 404


def test_a_missing_thumbnail_404s(client) -> None:
    assert client.get("/tickets/26-0001/photos/deadbeef.jpg/thumb").status_code == 404


# --- protection -------------------------------------------------------------


def test_photos_are_not_reachable_signed_out(db_path, photo_store) -> None:
    """The whole reason these are app routes and not a static mount."""

    # Seed a photo through a signed-in client.
    def _store():
        with connect(db_path) as conn:
            yield TicketStore(conn, year=26)

    app.dependency_overrides[get_store] = _store
    app.dependency_overrides[get_photo_store] = lambda: photo_store
    try:
        signed_in = sign_in(CsrfClient(app))
        signed_in.post(
            "/intake",
            data={
                "customer_name": "Private",
                "device": "Board",
                "phone": "555",
                "email": "",
                "return_address": "",
                "serial": "",
                "symptoms": "",
                "accessories": "",
                "condition_notes": "",
            },
            files=[("photos", ("a.jpg", _img(), "image/jpeg"))],
        )
        name = photo_store.list_photos("26-0001")[0].name

        signed_out = TestClient(app)
        for path in (
            f"/tickets/26-0001/photos/{name}",
            f"/tickets/26-0001/photos/{name}/thumb",
        ):
            response = signed_out.get(path, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers["location"].startswith("/login")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "name",
    ["../../../etc/passwd", "..%2f..%2fpasswd", "a/b.jpg", "....//x.jpg"],
)
def test_traversal_in_the_name_cannot_escape(client, name) -> None:
    """A crafted photo name must not read outside uploads/. Served or 404, never
    a file from elsewhere on the box."""
    response = client.get(f"/tickets/26-0001/photos/{name}", follow_redirects=False)

    assert response.status_code in (404, 403, 400)
    if response.status_code == 200:
        raise AssertionError("traversal served a file")
