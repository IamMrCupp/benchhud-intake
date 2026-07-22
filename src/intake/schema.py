"""Database schema and migration.

**Migrations use ``PRAGMA user_version`` and idempotent DDL, not Alembic.** One
table, a single writer, one deployment — Alembic would add a dependency and a
migrations tree for a problem this app doesn't have. ``user_version`` is a
SQLite built-in that costs nothing and is easy to replace later if the schema
gets complicated.

**The CHECK constraints are deliberate duplication.** The same rules are
enforced in Python (see ``intake.status``), but the database is the layer that
survives a future script, a manual ``sqlite3`` session, or a bug in the API. For
a file holding customer records, that's worth the redundancy.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

# Duplicated from intake.status, which doesn't exist yet — it lands in B.2 along
# with the test that asserts these two lists never drift apart. Until then this
# is the only definition, so treat it as the source of truth.
_STATUSES = (
    "awaiting_arrival",
    "intake",
    "diagnosing",
    "quoted",
    "awaiting_parts",
    "repairing",
    "ready",
    "closed",
)
_OUTCOMES = ("repaired", "unrepairable", "declined", "abandoned")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


_CREATE_TICKETS = f"""
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Refs render as YY-NNNN ("26-0007"). Stored as integers because the
    -- zero-padding is presentation: formatting on the way out means ticket
    -- 10000 widens gracefully instead of becoming a parsing bug.
    year            INTEGER NOT NULL,
    counter         INTEGER NOT NULL,

    device          TEXT    NOT NULL,
    serial          TEXT,
    symptoms        TEXT    NOT NULL DEFAULT '',

    customer_name   TEXT    NOT NULL,
    phone           TEXT,
    email           TEXT,
    -- Free text, not structured lines: it goes on a shipping label, and it is
    -- the most sensitive field here. Blank for walk-ins.
    return_address  TEXT,

    accessories     TEXT,
    condition_notes TEXT,

    status          TEXT    NOT NULL,
    outcome         TEXT,

    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,

    -- Counters restart each January, so uniqueness is the pair. This is also
    -- what makes ref allocation safe under concurrency: the database rejects a
    -- collision rather than the application hoping it read a fresh MAX().
    UNIQUE (year, counter),

    CONSTRAINT contactable CHECK (phone IS NOT NULL OR email IS NOT NULL),

    -- Both directions: closed implies an outcome, an outcome implies closed.
    CONSTRAINT outcome_iff_closed
        CHECK ((status = 'closed') = (outcome IS NOT NULL)),

    CONSTRAINT known_status  CHECK (status IN ({_quoted(_STATUSES)})),
    CONSTRAINT known_outcome
        CHECK (outcome IS NULL OR outcome IN ({_quoted(_OUTCOMES)}))
);
"""

# Open tickets are read on every poll from benchhud; closed ones accumulate
# forever and are never in that result set.
_CREATE_STATUS_INDEX = "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status);"


def initialise(conn: sqlite3.Connection) -> None:
    """Create the schema if absent. Safe to call on every startup."""
    if conn.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return

    with conn:
        conn.execute(_CREATE_TICKETS)
        conn.execute(_CREATE_STATUS_INDEX)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
