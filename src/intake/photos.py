"""Condition photos, stored on disk beside the database.

A picture of a cracked bezel is worth something at drop-off and worth nothing a
week later, so this exists to make capture fast enough that it actually happens
with a customer standing there.

**Everything here treats its inputs as hostile.** A ref arrives from a URL and a
filename arrives from a phone; both reach the filesystem, on a box that also
holds the customer database. Two independent defences:

1. Refs are validated against the known format, and filenames are *generated* —
   the client's name is used only to pick an extension.
2. Every resulting path is resolved and confirmed to sit inside the upload root.

Either alone would probably do. Together they cover the case where the first one
has a bug, which is the case worth planning for.
"""

from __future__ import annotations

import io
import secrets
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from intake.refs import parse_ref

#: Refuse anything larger. A modern phone photo is 3 to 12 MB; 25 covers a
#: high-resolution capture with room to spare, while keeping a Pi with an NFS
#: mount from being trivially filled by repeated uploads.
MAX_BYTES = 25 * 1024 * 1024

#: Longest edge of a thumbnail. Big enough to see a dent on a tablet, small
#: enough that a ticket page with a dozen photos stays quick over the LAN.
THUMBNAIL_MAX_EDGE = 400

#: What a browser will actually display, mapped to the extension we store.
_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}

_THUMB_DIR = ".thumbs"


class PhotoError(Exception):
    """Base for anything wrong with an upload."""


class UnsupportedPhoto(PhotoError):
    """Not an image, or not one a browser will show."""


class PhotoTooLarge(PhotoError):
    """Bigger than :data:`MAX_BYTES`."""


@dataclass(frozen=True)
class Photo:
    """One stored photo."""

    name: str
    ref: str


class PhotoStore:
    """Condition photos for tickets, under one root directory."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- writing ------------------------------------------------------------

    def save(self, ref: str, filename: str, data: bytes) -> Photo:
        """Store an upload against ``ref`` and build its thumbnail.

        ``filename`` is only ever a hint. The stored name is generated, so a
        crafted upload name can't become a path, collide with another photo, or
        overwrite anything.
        """
        directory = self._ticket_dir(ref, create=False)

        if len(data) > MAX_BYTES:
            # Checked before anything is written: rejecting after the fact would
            # make the limit useless as a disk guard.
            raise PhotoTooLarge(f"photo is larger than {MAX_BYTES // (1024 * 1024)} MB")

        image, extension = self._decode(data)

        directory.mkdir(parents=True, exist_ok=True)
        name = f"{secrets.token_hex(8)}{extension}"
        target = self._inside(directory / name)

        # Original bytes, untouched — this is evidence for a dispute, and a
        # re-encoded copy is weaker ground than what the camera produced.
        target.write_bytes(data)

        try:
            self._write_thumbnail(image, directory, name)
        except OSError:
            # A thumbnail failure must not cost the photo itself.
            target.unlink(missing_ok=True)
            raise

        return Photo(name=name, ref=ref)

    def _decode(self, data: bytes) -> tuple[Image.Image, str]:
        """Confirm the bytes really are a displayable image.

        Extension is a hint and content is the authority: a `.jpg` that isn't an
        image is refused, and a real PNG called `.txt` is stored as a PNG.
        """
        try:
            image = Image.open(io.BytesIO(data))
            image.verify()
            # verify() leaves the file unusable, so reopen for the thumbnail.
            image = Image.open(io.BytesIO(data))
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise UnsupportedPhoto("that file is not an image") from exc

        extension = _FORMATS.get(image.format or "")
        if extension is None:
            raise UnsupportedPhoto(f"{image.format or 'that file'} is not a supported image type")

        return image, extension

    def _write_thumbnail(self, image: Image.Image, directory: Path, name: str) -> None:
        """Write a bounded copy, with metadata dropped.

        Resized **on upload rather than on render**: a phone photo is several
        megabytes, and a Pi re-encoding those on every page view would make the
        ticket page unusable. The cost is paid once, where a moment of latency
        is invisible.

        Saving pixel data into a fresh image drops EXIF along the way — which
        matters more than it sounds, because phone photos carry GPS and a
        thumbnail of a customer's laptop tagged with their home address is a
        leak nobody would think to look for.
        """
        thumbs = self._inside(directory / _THUMB_DIR)
        thumbs.mkdir(parents=True, exist_ok=True)

        image = image.convert("RGB") if image.mode not in ("RGB", "L") else image
        image.thumbnail((THUMBNAIL_MAX_EDGE, THUMBNAIL_MAX_EDGE), Image.LANCZOS)

        # Pasting pixels into a fresh image is what drops the metadata; the new
        # image has no EXIF block to inherit one.
        clean = Image.new(image.mode, image.size)
        clean.paste(image)
        clean.save(self._inside(thumbs / f"{Path(name).stem}.jpg"), format="JPEG", quality=82)

    # -- reading ------------------------------------------------------------

    def list_photos(self, ref: str) -> list[Photo]:
        """Photos on a ticket, oldest first."""
        directory = self._ticket_dir(ref, create=False)
        if not directory.is_dir():
            return []

        return [
            Photo(name=path.name, ref=ref)
            for path in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime_ns)
            if path.is_file()
        ]

    def photo_path(self, ref: str, name: str) -> Path:
        """Where a photo lives on disk."""
        return self._inside(self._ticket_dir(ref, create=False) / self._safe_name(name))

    def thumbnail_path(self, ref: str, name: str) -> Path:
        """Where a photo's thumbnail lives."""
        directory = self._ticket_dir(ref, create=False) / _THUMB_DIR
        return self._inside(directory / f"{Path(self._safe_name(name)).stem}.jpg")

    # -- guards -------------------------------------------------------------

    def _ticket_dir(self, ref: str, create: bool = False) -> Path:
        """The directory for a ticket, with the ref validated first.

        Parsing the ref rather than sanitising it means only strings matching
        the real ``26-0007`` shape ever reach a path — a much smaller surface
        than trying to enumerate everything hostile.
        """
        year, counter = parse_ref(ref)
        directory = self._inside(self.root / f"{year:02d}-{counter:04d}")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    @staticmethod
    def _safe_name(name: str) -> str:
        """Require a single, plain path component.

        Deliberately refuses anything containing a separator rather than
        reducing it to a basename. Quietly turning ``../../../etc/passwd`` into
        ``passwd`` would be *safe* but dishonest — the caller asked for one
        thing and got another, and a bug that produces the wrong file silently
        is worse than one that raises.
        """
        if not name or name in (".", "..") or "\x00" in name:
            raise ValueError(f"unusable photo name: {name!r}")
        if "/" in name or "\\" in name or Path(name).name != name:
            raise ValueError(f"photo name must be a single path component: {name!r}")
        return name

    def _inside(self, path: Path) -> Path:
        """Confirm a path really sits under the upload root.

        The second of the two defences. Resolution catches what validation
        might miss — encoded separators, symlinks, anything that only becomes
        obvious once the OS has normalised it.
        """
        root = self.root.resolve()
        resolved = (root / path.relative_to(self.root)).resolve() if path.is_absolute() else path
        if not Path(resolved).resolve().is_relative_to(root):
            raise ValueError(f"path escapes the upload root: {path}")
        return path
