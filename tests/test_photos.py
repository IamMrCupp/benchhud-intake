"""Photo storage.

The traversal tests are the ones that matter. Everything else here is about
whether a feature works; those are about whether a crafted ref or filename can
write outside `uploads/` — on a box that also holds the customer database.

EXIF stripping gets its own section for a reason: a thumbnail of someone's
laptop carrying their home address is a leak nobody thinks to check for, in a
repo that exists specifically to keep customer data contained.
"""

import io

import pytest
from PIL import Image

from intake.photos import (
    MAX_BYTES,
    PhotoStore,
    PhotoTooLarge,
    UnsupportedPhoto,
)


def _image_bytes(fmt="JPEG", size=(1200, 900), colour=(120, 30, 30), **save_kwargs) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format=fmt, **save_kwargs)
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    return PhotoStore(tmp_path / "uploads")


# --- storing ----------------------------------------------------------------


def test_stores_a_jpeg(store) -> None:
    photo = store.save("26-0001", "IMG_1234.JPG", _image_bytes())

    assert photo.name.endswith(".jpg")


def test_stores_a_png(store) -> None:
    photo = store.save("26-0001", "shot.png", _image_bytes(fmt="PNG"))

    assert photo.name.endswith(".png")


def test_stored_file_exists_on_disk(store, tmp_path) -> None:
    photo = store.save("26-0001", "a.jpg", _image_bytes())

    assert (tmp_path / "uploads" / "26-0001" / photo.name).is_file()


def test_photos_are_grouped_by_ref(store, tmp_path) -> None:
    store.save("26-0001", "a.jpg", _image_bytes())
    store.save("26-0002", "b.jpg", _image_bytes())

    assert (tmp_path / "uploads" / "26-0001").is_dir()
    assert (tmp_path / "uploads" / "26-0002").is_dir()


def test_multiple_photos_on_one_ticket(store) -> None:
    for _ in range(3):
        store.save("26-0001", "same-name.jpg", _image_bytes())

    assert len(store.list_photos("26-0001")) == 3


def test_identical_names_do_not_overwrite(store) -> None:
    """Phone cameras reuse names — two boards both yielding IMG_0001.JPG must
    not silently replace each other's evidence."""
    first = store.save("26-0001", "IMG_0001.JPG", _image_bytes(colour=(10, 10, 10)))
    second = store.save("26-0001", "IMG_0001.JPG", _image_bytes(colour=(200, 200, 200)))

    assert first.name != second.name


def test_listing_is_empty_for_an_unknown_ticket(store) -> None:
    assert store.list_photos("26-9999") == []


def test_listing_is_ordered(store) -> None:
    names = [store.save("26-0001", f"{i}.jpg", _image_bytes()).name for i in range(4)]

    assert [p.name for p in store.list_photos("26-0001")] == names


# --- content validation -----------------------------------------------------


def test_rejects_a_non_image_named_jpg(store) -> None:
    """Extension is a hint, never evidence."""
    with pytest.raises(UnsupportedPhoto):
        store.save("26-0001", "evil.jpg", b"#!/bin/sh\nrm -rf /\n")


def test_rejects_an_empty_file(store) -> None:
    with pytest.raises(UnsupportedPhoto):
        store.save("26-0001", "empty.jpg", b"")


def test_rejects_html_disguised_as_an_image(store) -> None:
    with pytest.raises(UnsupportedPhoto):
        store.save("26-0001", "x.png", b"<html><script>alert(1)</script></html>")


def test_accepts_a_real_image_with_a_wrong_extension(store) -> None:
    """A real PNG called .txt is still a photo; trust the bytes."""
    photo = store.save("26-0001", "notes.txt", _image_bytes(fmt="PNG"))

    assert photo.name.endswith(".png")


def test_rejects_an_oversized_file(store) -> None:
    with pytest.raises(PhotoTooLarge):
        store.save("26-0001", "big.jpg", b"\xff\xd8\xff" + b"\x00" * MAX_BYTES)


def test_an_oversized_file_leaves_nothing_behind(store, tmp_path) -> None:
    """Rejecting after writing would make the size limit useless as a disk guard."""
    with pytest.raises(PhotoTooLarge):
        store.save("26-0001", "big.jpg", b"\xff\xd8\xff" + b"\x00" * MAX_BYTES)

    assert not list((tmp_path / "uploads").rglob("*.jpg"))


def test_a_rejected_upload_leaves_no_partial_file(store, tmp_path) -> None:
    with pytest.raises(UnsupportedPhoto):
        store.save("26-0001", "bad.jpg", b"not an image at all")

    assert not list((tmp_path / "uploads").rglob("*.*"))


# --- path traversal ---------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "../etc",
        "../../etc/passwd",
        "..%2f..%2fetc",
        "/etc/passwd",
        "26-0001/../../..",
        "....//....//etc",
        "26-0001\x00/etc",
        ".",
        "..",
        "",
    ],
)
def test_a_hostile_ref_cannot_escape(store, ref) -> None:
    with pytest.raises((ValueError, UnsupportedPhoto)):
        store.save(ref, "a.jpg", _image_bytes())


@pytest.mark.parametrize(
    "filename",
    [
        "../../../etc/passwd.jpg",
        "/etc/passwd.jpg",
        "..\\..\\windows\\system32.jpg",
        "a/b/c.jpg",
        "\x00.jpg",
    ],
)
def test_a_hostile_filename_cannot_escape(store, filename, tmp_path) -> None:
    """The client's filename is only ever a hint for the extension — it must
    never become a path component."""
    photo = store.save("26-0001", filename, _image_bytes())

    resolved = (tmp_path / "uploads" / "26-0001" / photo.name).resolve()
    assert resolved.is_relative_to((tmp_path / "uploads").resolve())
    assert "/" not in photo.name and "\\" not in photo.name


def test_listing_a_hostile_ref_raises(store) -> None:
    with pytest.raises(ValueError):
        store.list_photos("../../etc")


def test_resolving_a_hostile_ref_raises(store) -> None:
    with pytest.raises(ValueError):
        store.photo_path("../../etc", "passwd")


def test_resolving_a_hostile_name_raises(store) -> None:
    with pytest.raises(ValueError):
        store.photo_path("26-0001", "../../../etc/passwd")


def test_every_stored_path_stays_inside_uploads(store, tmp_path) -> None:
    """The belt-and-braces check: whatever the input, the resolved path is
    inside the upload root. Validation catches the obvious; resolution catches
    encoding tricks and symlinks."""
    root = (tmp_path / "uploads").resolve()

    for name in ["ok.jpg", "../up.jpg", "a/b.jpg", "....//x.jpg"]:
        photo = store.save("26-0001", name, _image_bytes())
        assert store.photo_path("26-0001", photo.name).resolve().is_relative_to(root)


# --- thumbnails -------------------------------------------------------------


def test_a_thumbnail_is_created(store) -> None:
    photo = store.save("26-0001", "a.jpg", _image_bytes())

    assert store.thumbnail_path("26-0001", photo.name).is_file()


def test_the_thumbnail_is_smaller_than_the_original(store) -> None:
    photo = store.save("26-0001", "a.jpg", _image_bytes(size=(4000, 3000)))

    original = store.photo_path("26-0001", photo.name).stat().st_size
    thumb = store.thumbnail_path("26-0001", photo.name).stat().st_size
    assert thumb < original


def test_the_thumbnail_is_bounded(store) -> None:
    photo = store.save("26-0001", "a.jpg", _image_bytes(size=(4000, 3000)))

    with Image.open(store.thumbnail_path("26-0001", photo.name)) as img:
        assert max(img.size) <= 400


def test_the_original_is_kept_unchanged(store) -> None:
    """Evidence for a dispute. A re-encoded copy is weaker ground than the file
    the camera produced."""
    raw = _image_bytes(size=(2000, 1500))
    photo = store.save("26-0001", "a.jpg", raw)

    assert store.photo_path("26-0001", photo.name).read_bytes() == raw


def test_a_small_image_is_not_upscaled(store) -> None:
    photo = store.save("26-0001", "tiny.jpg", _image_bytes(size=(80, 60)))

    with Image.open(store.thumbnail_path("26-0001", photo.name)) as img:
        assert img.size == (80, 60)


# --- EXIF -------------------------------------------------------------------


def test_gps_exif_does_not_survive_into_the_thumbnail(store) -> None:
    """A thumbnail of a customer's laptop tagged with their home address is a
    leak nobody would think to look for."""
    exif = Image.Exif()
    exif[0x8825] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}
    raw = _image_bytes(exif=exif.tobytes())

    photo = store.save("26-0001", "geo.jpg", raw)

    with Image.open(store.thumbnail_path("26-0001", photo.name)) as img:
        assert not img.getexif().get_ifd(0x8825)
