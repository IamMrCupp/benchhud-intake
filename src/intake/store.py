"""Reading and writing tickets.

This is the layer Phase 2's intake form and Phase 5's benchhud API both sit on,
which is why validation lives here rather than in the form. A rule enforced only
in the web form isn't a rule — the API, a migration script and a future import
job are all callers too.

``Ticket`` here is the store's own shape and a **superset** of
``benchhud.tickets.Ticket``. The benchhud one is a deliberately lossy projection
built in Phase 5 (``research.md`` §2); the fields it omits — contact details,
return address — are the ones that must never reach a machine that composites to
a video stream.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from intake.refs import allocate_counter, format_ref, parse_ref
from intake.status import (
    OPEN_STATUSES,
    Outcome,
    Status,
    to_outcome,
    to_status,
    validate_outcome,
)

#: Intake details that can be edited after the fact.
#:
#: `status` and `outcome` are deliberately absent — they move through
#: `set_status`, which is where the outcome invariant is enforced. Letting them
#: through here would be a second, unguarded path to the same state.
_EDITABLE = frozenset(
    {
        "device",
        "serial",
        "symptoms",
        "customer_name",
        "phone",
        "email",
        "return_address",
        "accessories",
        "condition_notes",
        "board_id",
    }
)

_COLUMNS = (
    "year, counter, device, serial, symptoms, customer_name, phone, email,"
    " return_address, accessories, condition_notes, status, outcome,"
    " created_at, updated_at, board_id"
)


@dataclass(frozen=True)
class TicketLink:
    """A reference to where a ticket's board docs live — a boardview, a
    schematic, a source to download from."""

    id: int
    label: str
    url: str


@dataclass(frozen=True)
class Ticket:
    """One piece of work, as the store sees it."""

    ref: str
    device: str
    symptoms: str
    customer_name: str
    status: Status
    created_at: str
    updated_at: str
    phone: str | None = None
    email: str | None = None
    serial: str | None = None
    return_address: str | None = None
    accessories: str | None = None
    condition_notes: str | None = None
    board_id: str | None = None
    outcome: Outcome | None = None


def _now() -> str:
    """UTC, ISO-8601, second precision.

    SQLite has no datetime type. A single sortable string format beats letting
    each caller pick one.
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _clean(value: str | None) -> str | None:
    """Normalise blank-ish input to None. A form posts '' where a script posts None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _row_to_ticket(row: sqlite3.Row) -> Ticket:
    return Ticket(
        ref=format_ref(row["year"], row["counter"]),
        device=row["device"],
        symptoms=row["symptoms"],
        customer_name=row["customer_name"],
        status=Status(row["status"]),
        outcome=Outcome(row["outcome"]) if row["outcome"] else None,
        phone=row["phone"],
        email=row["email"],
        serial=row["serial"],
        return_address=row["return_address"],
        accessories=row["accessories"],
        condition_notes=row["condition_notes"],
        board_id=row["board_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class TicketStore:
    """Ticket persistence for one intake year."""

    def __init__(self, conn: sqlite3.Connection, year: int | None = None) -> None:
        self.conn = conn
        #: Injectable so tests don't depend on the wall clock, and so a January
        #: rollover can be exercised without waiting for January.
        self.year = year if year is not None else datetime.now(UTC).year % 100

    # -- writing ------------------------------------------------------------

    def create_ticket(
        self,
        *,
        device: str,
        symptoms: str = "",
        customer_name: str,
        phone: str | None = None,
        email: str | None = None,
        serial: str | None = None,
        return_address: str | None = None,
        accessories: str | None = None,
        condition_notes: str | None = None,
        board_id: str | None = None,
    ) -> Ticket:
        """Log a new piece of work and give it a ref."""
        device = (device or "").strip()
        customer_name = (customer_name or "").strip()
        phone, email = _clean(phone), _clean(email)

        # Validate before allocating: a rejected create shouldn't burn a ref and
        # leave a hole in the numbering.
        if not device:
            raise ValueError("device is required")
        if not customer_name:
            raise ValueError("customer_name is required")
        if phone is None and email is None:
            raise ValueError("a ticket needs at least one of phone or email")

        counter = allocate_counter(self.conn, self.year)
        stamp = _now()

        with self.conn:
            self.conn.execute(
                f"INSERT INTO tickets ({_COLUMNS}) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.year,
                    counter,
                    device,
                    _clean(serial),
                    (symptoms or "").strip(),
                    customer_name,
                    phone,
                    email,
                    _clean(return_address),
                    _clean(accessories),
                    _clean(condition_notes),
                    Status.INTAKE.value,
                    None,
                    stamp,
                    stamp,
                    _clean(board_id),
                ),
            )

        return self.get_by_ref(format_ref(self.year, counter))  # type: ignore[return-value]

    def set_status(
        self,
        ref: str,
        status: Status | str,
        outcome: Outcome | str | None = None,
    ) -> Ticket:
        """Move a ticket through the workflow.

        The graph is deliberately permissive between open states: a quote gets
        declined, a part arrives wrong, a board comes back a week later. What's
        enforced is the outcome invariant, not the order.

        **Reopening clears the outcome** — a ticket that isn't finished can't
        still record how it finished.
        """
        self._require(ref)

        # Coerce before validating: callers pass strings straight off a form or
        # a query param, and Status is a StrEnum, so an identity check against a
        # raw string would silently skip the invariant.
        status = to_status(status)
        outcome = to_outcome(outcome)

        # An outcome alongside an open status is a caller bug and should say so
        # rather than being quietly dropped. Omitting it while moving to an open
        # status is the reopen path, and writes NULL over whatever was there.
        validate_outcome(status, outcome)

        stamp = _now()
        year, counter = parse_ref(ref)
        with self.conn:
            self.conn.execute(
                "UPDATE tickets SET status = ?, outcome = ?, updated_at = ?"
                " WHERE year = ? AND counter = ?",
                (status.value, outcome.value if outcome else None, stamp, year, counter),
            )

        return self.get_by_ref(ref)  # type: ignore[return-value]

    def update_ticket(self, ref: str, **fields: str | None) -> Ticket:
        """Edit intake details after the fact.

        Only the fields in ``_EDITABLE`` — status changes go through
        ``set_status`` so there's exactly one guarded path to a closed ticket.
        An unknown field name raises rather than being ignored, so a typo'd
        kwarg can't silently do nothing.
        """
        current = self._require(ref)

        unknown = set(fields) - _EDITABLE
        if unknown:
            raise ValueError(f"not editable: {', '.join(sorted(unknown))}")
        if not fields:
            return current

        # Re-check the invariants the record was created under, against the
        # post-edit values. An edit is just another way to reach an invalid
        # state — blanking the only contact method strands the customer.
        def after(name: str, existing: str | None) -> str | None:
            return fields.get(name, existing)

        device = (after("device", current.device) or "").strip()
        customer_name = (after("customer_name", current.customer_name) or "").strip()
        phone = _clean(after("phone", current.phone))
        email = _clean(after("email", current.email))
        if not device:
            raise ValueError("device is required")
        if not customer_name:
            raise ValueError("customer_name is required")
        if phone is None and email is None:
            raise ValueError("a ticket needs at least one of phone or email")

        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [_clean(v) if isinstance(v, str) or v is None else v for v in fields.values()]
        year, counter = parse_ref(ref)

        with self.conn:
            self.conn.execute(
                f"UPDATE tickets SET {assignments}, updated_at = ? WHERE year = ? AND counter = ?",
                (*values, _now(), year, counter),
            )

        return self.get_by_ref(ref)  # type: ignore[return-value]

    def _require(self, ref: str) -> Ticket:
        """Fetch a ticket that must exist.

        Reading a missing ticket is normal (``get_by_ref`` returns ``None``);
        *writing* to one is a caller bug, so this raises instead.
        """
        ticket = self.get_by_ref(ref)
        if ticket is None:
            raise LookupError(f"no such ticket: {ref}")
        return ticket

    # -- doc links ----------------------------------------------------------

    def add_link(self, ref: str, label: str, url: str) -> TicketLink:
        """Attach a doc reference to a ticket.

        A link needs a URL; the label is convenience — a bare URL is still
        usable, so an omitted label defaults rather than forcing busywork at the
        bench.
        """
        self._require(ref)
        url = (url or "").strip()
        if not url:
            raise ValueError("a link needs a URL")
        label = (label or "").strip() or "Link"

        year, counter = parse_ref(ref)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO ticket_links (year, counter, label, url, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (year, counter, label, url, _now()),
            )
        return TicketLink(id=cur.lastrowid, label=label, url=url)

    def list_links(self, ref: str) -> list[TicketLink]:
        """A ticket's doc links, oldest first."""
        year, counter = parse_ref(ref)
        rows = self.conn.execute(
            "SELECT id, label, url FROM ticket_links WHERE year = ? AND counter = ? ORDER BY id",
            (year, counter),
        )
        return [TicketLink(id=r["id"], label=r["label"], url=r["url"]) for r in rows]

    def remove_link(self, ref: str, link_id: int) -> None:
        """Remove a link. Removing one that isn't there is a no-op, not an error
        — the caller wanted it gone, and it is."""
        year, counter = parse_ref(ref)
        with self.conn:
            self.conn.execute(
                "DELETE FROM ticket_links WHERE id = ? AND year = ? AND counter = ?",
                (link_id, year, counter),
            )

    # -- reading ------------------------------------------------------------

    def get_by_ref(self, ref: str) -> Ticket | None:
        """Look up one ticket. ``None`` when it doesn't exist.

        A miss is a normal outcome, not an error — benchhud's ``get_ticket()``
        treats it as its 404 path. A *malformed* ref is different: that's a
        caller bug, and silently returning ``None`` would hide it.
        """
        year, counter = parse_ref(ref)
        row = self.conn.execute(
            f"SELECT {_COLUMNS} FROM tickets WHERE year = ? AND counter = ?",
            (year, counter),
        ).fetchone()

        return _row_to_ticket(row) if row else None

    def list_tickets(
        self,
        *,
        status: Status | str | None = None,
        open_only: bool = False,
    ) -> list[Ticket]:
        """Tickets, newest first — the bench cares about what just came in.

        ``open_only`` is the set Phase 5 serves as ``?status=open``. It's built
        from ``OPEN_STATUSES``, which derives itself from the enum, so a new
        status can't quietly drop out of the bench queue.
        """
        sql = f"SELECT {_COLUMNS} FROM tickets"
        params: list[object] = []

        if status is not None:
            sql += " WHERE status = ?"
            params.append(to_status(status).value)
        elif open_only:
            marks = ", ".join("?" * len(OPEN_STATUSES))
            sql += f" WHERE status IN ({marks})"
            params.extend(sorted(s.value for s in OPEN_STATUSES))

        sql += " ORDER BY year DESC, counter DESC"
        return [_row_to_ticket(row) for row in self.conn.execute(sql, params)]
