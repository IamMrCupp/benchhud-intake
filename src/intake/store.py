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
from intake.status import OPEN_STATUSES, Outcome, Status

_COLUMNS = (
    "year, counter, device, serial, symptoms, customer_name, phone, email,"
    " return_address, accessories, condition_notes, status, outcome,"
    " created_at, updated_at"
)


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
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )

        return self.get_by_ref(format_ref(self.year, counter))  # type: ignore[return-value]

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
        status: Status | None = None,
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
            params.append(status.value)
        elif open_only:
            marks = ", ".join("?" * len(OPEN_STATUSES))
            sql += f" WHERE status IN ({marks})"
            params.extend(sorted(s.value for s in OPEN_STATUSES))

        sql += " ORDER BY year DESC, counter DESC"
        return [_row_to_ticket(row) for row in self.conn.execute(sql, params)]
