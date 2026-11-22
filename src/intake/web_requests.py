"""Repair requests from the public site, before they are tickets.

The shop's public site has a "request a repair" form. This service still has no
public route: a relay that lives with the site takes the form, and forwards it
here over the private network with a token that can write a request and read
nothing. This module is what that request lands in.

**A request is not a ticket.** It sits in its own table, gets an opaque id
rather than a ref, and never appears in ``/api/tickets``. Someone at the bench
looks at it and accepts or dismisses it; a ref is allocated only on accept. See
``intake.schema`` for why the separation is load-bearing.

**The shape here is a contract with another repository.** The field names, the
caps and the error codes are pinned in ``tests/fixtures/request-contract.json``,
and ``tests/test_request_contract.py`` fails if this module and that file
disagree. Change them together, and tell the site repo.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass

from intake.refs import format_ref, parse_ref
from intake.status import Status
from intake.store import Ticket, TicketStore, _clean, _now

#: The states a request moves through. It starts pending and ends in one of
#: the other two; there is no way back.
STATES = ("pending", "accepted", "dismissed")

#: How the device gets to the bench.
HANDOFFS = ("drop_off", "mail_in")

#: Every field a submission may carry, in the order errors are reported.
FIELDS = (
    "customer_name",
    "email",
    "phone",
    "device",
    "symptoms",
    "handoff",
    "return_address",
)

#: Fields that must be present and non-blank. ``email`` / ``phone`` are handled
#: apart: at least one of the two, the same ``contactable`` rule tickets have.
REQUIRED = ("customer_name", "device", "symptoms", "handoff")

#: Caps, in characters, measured after surrounding whitespace is stripped.
#: Tickets have none because the person typing is signed in at the bench; this
#: text comes from strangers.
MAX_LENGTHS = {
    "customer_name": 120,
    "email": 254,
    "phone": 40,
    "device": 200,
    "symptoms": 4000,
    "return_address": 500,
}

#: The request body is a form's worth of text, never an upload.
MAX_BODY_BYTES = 16 * 1024

IDEMPOTENCY_HEADER = "Idempotency-Key"
MAX_IDEMPOTENCY_KEY_LENGTH = 200

MAX_PENDING_ENV_VAR = "INTAKE_MAX_PENDING_REQUESTS"
DEFAULT_MAX_PENDING = 200


def max_pending() -> int:
    """How many requests may wait for review before new ones get a ``429``.

    A backstop that doesn't depend on the relay behaving. Per-IP limiting is the
    relay's job; this only stops a broken or abused relay from filling the
    database with rows nobody has looked at. Anything that isn't a whole number
    of zero or more falls back to the default rather than to "no cap".
    """
    raw = os.environ.get(MAX_PENDING_ENV_VAR, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PENDING
    return value if value >= 0 else DEFAULT_MAX_PENDING


#: Every code an error can carry. The contract fixture lists the same set.
ERROR_CODES = ("required", "one_required", "too_long", "invalid", "invalid_json", "unknown_field")


@dataclass(frozen=True)
class FieldError:
    """One thing wrong with a submission: which field, and a fixed code.

    Never the value. The relay forwards text typed by strangers, and an error
    that echoes it back would put that text in the relay's logs.
    """

    field: str
    code: str

    def __post_init__(self) -> None:
        # A code the contract doesn't list is a contract change, not a typo to
        # find out about from the relay.
        if self.code not in ERROR_CODES:
            raise ValueError(f"not a contract error code: {self.code}")

    def as_json(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code}


class InvalidSubmission(ValueError):
    """A submission that broke the contract. Carries every error, not the first."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("invalid request")
        self.errors = errors


class PendingCapReached(Exception):
    """Too many requests are already waiting for review."""


def _storable(value: str) -> bool:
    """Whether a string can be written as UTF-8.

    JSON can spell half of a surrogate pair (``"\\ud800"``), which Python holds
    happily and SQLite refuses. A broken emoji from a browser form is enough.
    """
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


class NotPending(ValueError):
    """Accepting or dismissing a request that has already been dealt with."""


def validate(payload: object) -> dict[str, str | None]:
    """Check a decoded JSON body against the contract and return clean fields.

    Values are cleaned the way tickets are (``_clean``: stripped, blank becomes
    ``None``) and then capped. A field that isn't a string is ``invalid``: JSON
    lets a client send a number or a list where text belongs.
    """
    if not isinstance(payload, dict):
        raise InvalidSubmission([FieldError("body", "invalid_json")])

    found: dict[str, str] = {}
    cleaned: dict[str, str | None] = {}

    for name in FIELDS:
        value = payload.get(name)
        cleaned[name] = None
        if value is not None and not isinstance(value, str):
            found[name] = "invalid"
            continue
        value = _clean(value)
        if value is None:
            if name in REQUIRED:
                found[name] = "required"
        elif not _storable(value):
            found[name] = "invalid"
        elif name in MAX_LENGTHS and len(value) > MAX_LENGTHS[name]:
            found[name] = "too_long"
        elif name == "handoff" and value not in HANDOFFS:
            found[name] = "invalid"
        else:
            cleaned[name] = value

    # At least one way to reach them. Reported against both fields, since
    # either one would fix it. Skipped when one of the two was sent but wrong:
    # that field already has its own error.
    if (
        cleaned["email"] is None
        and cleaned["phone"] is None
        and not {"email", "phone"} & set(found)
    ):
        found["email"] = found["phone"] = "one_required"

    # Always in the contract's field order, whichever rule found them.
    errors = [FieldError(name, found[name]) for name in FIELDS if name in found]

    # A key this contract doesn't know is a relay out of step with it. Said
    # without naming the key, which is the client's text.
    if set(payload) - set(FIELDS):
        errors.append(FieldError("body", "unknown_field"))

    if errors:
        raise InvalidSubmission(errors)
    return cleaned


def clean_idempotency_key(raw: str | None) -> str | None:
    """The ``Idempotency-Key`` header, or ``None`` when it wasn't sent."""
    key = _clean(raw)
    if key is not None and len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise InvalidSubmission([FieldError(IDEMPOTENCY_HEADER, "too_long")])
    return key


@dataclass(frozen=True)
class WebRequest:
    """One request, as the store sees it.

    ``id`` is the opaque public id, the one the relay was given. An accepted
    request is a stub: its details are ``None`` and ``ticket_ref`` says where
    they went.
    """

    id: str
    handoff: str
    state: str
    received_at: str
    customer_name: str | None = None
    phone: str | None = None
    email: str | None = None
    device: str | None = None
    symptoms: str | None = None
    return_address: str | None = None
    resolved_at: str | None = None
    ticket_ref: str | None = None


_COLUMNS = (
    "public_id, customer_name, phone, email, device, symptoms, handoff,"
    " return_address, state, received_at, resolved_at, ticket_year, ticket_counter"
)


def _row_to_request(row: sqlite3.Row) -> WebRequest:
    ticket_ref = None
    if row["ticket_year"] is not None and row["ticket_counter"] is not None:
        ticket_ref = format_ref(row["ticket_year"], row["ticket_counter"])
    return WebRequest(
        id=row["public_id"],
        customer_name=row["customer_name"],
        phone=row["phone"],
        email=row["email"],
        device=row["device"],
        symptoms=row["symptoms"],
        handoff=row["handoff"],
        return_address=row["return_address"],
        state=row["state"],
        received_at=row["received_at"],
        resolved_at=row["resolved_at"],
        ticket_ref=ticket_ref,
    )


class RequestStore:
    """Request persistence. Shares the ticket store's connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # -- writing ------------------------------------------------------------

    def submit(
        self,
        fields: dict[str, str | None],
        *,
        idempotency_key: str | None = None,
        cap: int | None = None,
    ) -> tuple[str, bool]:
        """Store a validated submission as a pending request.

        Returns ``(public_id, created)``. A key seen before returns the first
        request's id and ``created=False``: a relay that timed out and tried
        again gets the same answer and no second row. The replay is checked
        before the cap, so a retry of something already stored still succeeds
        when the queue has since filled.

        ``fields`` must have come through :func:`validate`.
        """
        if idempotency_key is not None:
            existing = self._id_for_key(idempotency_key)
            if existing is not None:
                return existing, False

        cap = max_pending() if cap is None else cap
        public_id = secrets.token_urlsafe(16)

        # The cap is a condition on the INSERT itself, so counting and writing
        # are one statement with no gap for a second submission to slip through.
        try:
            with self.conn:
                cur = self.conn.execute(
                    "INSERT INTO requests (public_id, idempotency_key, customer_name,"
                    " phone, email, device, symptoms, handoff, return_address,"
                    " state, received_at)"
                    " SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?"
                    " WHERE (SELECT COUNT(*) FROM requests WHERE state = 'pending') < ?",
                    (
                        public_id,
                        idempotency_key,
                        fields["customer_name"],
                        fields["phone"],
                        fields["email"],
                        fields["device"],
                        fields["symptoms"],
                        fields["handoff"],
                        fields["return_address"],
                        _now(),
                        cap,
                    ),
                )
        except sqlite3.IntegrityError:
            # Two copies of the same retry landed together and the other one
            # won the UNIQUE key. Its row is the answer.
            existing = self._id_for_key(idempotency_key) if idempotency_key else None
            if existing is None:
                raise
            return existing, False

        if cur.rowcount == 0:
            raise PendingCapReached
        return public_id, True

    def accept(self, public_id: str, tickets: TicketStore) -> Ticket:
        """Turn a pending request into a real ticket.

        The ticket is made by ``create_ticket``, the same way the intake form
        makes one, so this is the moment a ref is allocated and not before.

        **The ticket starts at ``awaiting_arrival``, for drop-off and mail-in
        alike.** Accepting says "yes, bring it" or "yes, post it"; either way
        the device isn't on the bench yet. Move it to ``intake`` when it is.

        **The request is left as a stub.** Its name, contact details, device
        and symptoms are blanked here, because the ticket holds them now and a
        second copy of a customer's details is one more thing to leak. What
        stays is the link to the ticket, how it was coming in, and the
        idempotency key, so a relay retrying late still can't make a duplicate.
        """
        request = self._require_pending(public_id)

        # Claim it first, in one statement, so two accepts at once (a double
        # tap, two people) can't both get through: only one UPDATE finds the
        # row still pending. The claim parks it as dismissed, because the
        # table won't hold "accepted" without a ticket to point at. If
        # anything dies between here and the end, the request is left
        # dismissed with every detail intact and no ticket made. That can be
        # read and re-entered by hand; a duplicate ticket with a burned ref
        # can't be taken back.
        with self.conn:
            claimed = self.conn.execute(
                "UPDATE requests SET state = 'dismissed', resolved_at = ?"
                " WHERE public_id = ? AND state = 'pending'",
                (_now(), public_id),
            ).rowcount
        if not claimed:
            raise NotPending("this request was already dealt with")

        try:
            ticket = tickets.create_ticket(
                device=request.device or "",
                symptoms=request.symptoms or "",
                customer_name=request.customer_name or "",
                phone=request.phone,
                email=request.email,
                return_address=request.return_address,
                status=Status.AWAITING_ARRIVAL,
            )
        except Exception:
            # No ticket was made, so nothing was decided: put it back.
            with self.conn:
                self.conn.execute(
                    "UPDATE requests SET state = 'pending', resolved_at = NULL WHERE public_id = ?",
                    (public_id,),
                )
            raise

        year, counter = parse_ref(ticket.ref)
        with self.conn:
            self.conn.execute(
                "UPDATE requests SET state = 'accepted', resolved_at = ?,"
                " ticket_year = ?, ticket_counter = ?,"
                " customer_name = NULL, phone = NULL, email = NULL, device = NULL,"
                " symptoms = NULL, return_address = NULL"
                " WHERE public_id = ?",
                (_now(), year, counter, public_id),
            )
        return ticket

    def dismiss(self, public_id: str) -> WebRequest:
        """Mark a pending request as not becoming a ticket.

        Its details stay for the retention window, in case it was dismissed by
        mistake, and are deleted after that.
        """
        self._require_pending(public_id)
        with self.conn:
            self.conn.execute(
                "UPDATE requests SET state = 'dismissed', resolved_at = ? WHERE public_id = ?",
                (_now(), public_id),
            )
        return self.get(public_id)  # type: ignore[return-value]

    def _require_pending(self, public_id: str) -> WebRequest:
        request = self.get(public_id)
        if request is None:
            raise LookupError("no such request")
        if request.state != "pending":
            raise NotPending(f"this request was already {request.state}")
        return request

    def _id_for_key(self, idempotency_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT public_id FROM requests WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return row["public_id"] if row else None

    # -- reading ------------------------------------------------------------

    def count_pending(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM requests WHERE state = 'pending'"
        ).fetchone()[0]

    def get(self, public_id: str) -> WebRequest | None:
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM requests WHERE public_id = ?", (public_id,)
        ).fetchone()
        return _row_to_request(row) if row else None

    def list_requests(self, state: str = "pending") -> list[WebRequest]:
        """Requests in one state, oldest first: the one waiting longest is on top."""
        if state not in STATES:
            raise ValueError(f"unknown request state: {state}")
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM requests WHERE state = ? ORDER BY id", (state,)
        )
        return [_row_to_request(row) for row in rows]
