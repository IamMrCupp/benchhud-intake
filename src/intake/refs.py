"""Ticket references — ``26-0007``.

Two digits of year, a dash, then a counter that restarts each January. The
format is chosen for humans: it's short enough to write on a strip of tape, and
unambiguous enough to read out over the phone without spelling anything.

Padding is **presentation only**. Year and counter are stored as integers, so
the four-digit width is a formatting choice rather than a limit — ticket 10000
simply renders wider.
"""

from __future__ import annotations

import re
import sqlite3

_COUNTER_WIDTH = 4

# Two-digit year, dash, at least one digit. Deliberately not anchored to exactly
# four digits: parsing must keep working past 9999.
_REF_PATTERN = re.compile(r"^(\d{2})-(\d+)$")


def format_ref(year: int, counter: int) -> str:
    """Render a ref for a label, a phone call, or a URL."""
    return f"{year:02d}-{counter:0{_COUNTER_WIDTH}d}"


def parse_ref(ref: str) -> tuple[int, int]:
    """Parse ``26-0007`` into ``(26, 7)``.

    Surrounding whitespace is tolerated because refs get typed by hand and
    pasted out of messages. Anything else is an error rather than a guess —
    silently accepting a malformed ref means looking up the wrong customer.
    """
    match = _REF_PATTERN.match(ref.strip())
    if match is None:
        raise ValueError(f"malformed ticket ref: {ref!r}")

    year, counter = int(match.group(1)), int(match.group(2))
    if counter < 1:
        raise ValueError(f"ticket refs are 1-based, got: {ref!r}")

    return year, counter


def allocate_counter(conn: sqlite3.Connection, year: int) -> int:
    """Claim the next counter for ``year``, atomically.

    Backed by ``ref_counters``, which only ever climbs. Two properties matter:

    - **No reuse.** Deleting a ticket doesn't free its number. A ref that's been
      spoken to a customer and written on tape must never point at a different
      board.
    - **No race.** The increment is a single UPDATE, so there's no read-then-write
      gap for a concurrent caller to slip into. The deployment is single-writer
      today, but "unlikely" and "impossible" are different things when the
      failure mode is a lost ticket.
    """
    with conn:
        conn.execute("INSERT OR IGNORE INTO ref_counters (year) VALUES (?)", (year,))
        row = conn.execute(
            "UPDATE ref_counters SET last_counter = last_counter + 1"
            " WHERE year = ? RETURNING last_counter",
            (year,),
        ).fetchone()

    return row["last_counter"]
