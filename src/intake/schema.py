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

SCHEMA_VERSION = 3

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

    -- The board this device is, e.g. "820-00239". Job metadata benchhud will
    -- resolve boardview/schematic docs against; often unknown until the board
    -- is opened up, so nullable and usually filled later.
    board_id        TEXT,

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

# A monotonic sequence per year, deliberately separate from the tickets table.
#
# The obvious alternative — MAX(counter) + 1 over tickets — reuses a ref if the
# highest ticket is ever deleted. Refs get spoken aloud and written on tape, so
# a reused one points at the wrong customer's board. A counter that only ever
# climbs can't do that, and it makes allocation atomic as a side effect: a
# single UPDATE is its own transaction, with no read-then-write gap to race in.
_CREATE_REF_COUNTERS = """
CREATE TABLE IF NOT EXISTS ref_counters (
    year         INTEGER PRIMARY KEY,
    last_counter INTEGER NOT NULL DEFAULT 0
);
"""


# Doc links live in their own table (one ticket, many links) rather than a
# column, because you often add them over time — a schematic today, a boardview
# once you've sourced it. Keyed to the ticket by (year, counter), which the
# tickets table already makes UNIQUE.
_CREATE_TICKET_LINKS = """
CREATE TABLE IF NOT EXISTS ticket_links (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    year       INTEGER NOT NULL,
    counter    INTEGER NOT NULL,
    label      TEXT    NOT NULL,
    url        TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    FOREIGN KEY (year, counter) REFERENCES tickets (year, counter) ON DELETE CASCADE
);
"""


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """``ALTER TABLE ADD COLUMN``, but a no-op when the column already exists.

    A fresh database gets the column from ``CREATE TABLE`` and this does nothing;
    a database upgraded from an older version has the table but not the column,
    and this adds it without a rebuild — so existing rows are untouched. Checking
    ``table_info`` rather than catching the duplicate-column error keeps repeated
    ``initialise()`` calls clean.
    """
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def initialise(conn: sqlite3.Connection) -> None:
    """Create or upgrade the schema. Safe to call on every startup.

    Base tables use ``IF NOT EXISTS`` (idempotent create); changes to an
    *existing* table are version-gated ``ALTER``s that no-op when already
    applied. A database at the current version short-circuits; an older one
    picks up only what it's missing. No downgrade path — rolling a schema back
    is a restore-from-backup problem, not a migration one.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current >= SCHEMA_VERSION:
        return

    with conn:
        conn.execute(_CREATE_TICKETS)
        conn.execute(_CREATE_STATUS_INDEX)
        conn.execute(_CREATE_REF_COUNTERS)
        conn.execute(_CREATE_TICKET_LINKS)

        # v3: board_id on tickets. Fresh databases already have it from
        # _CREATE_TICKETS; a v2 database gains it here without a rebuild.
        if current < 3:
            _add_column_if_missing(conn, "tickets", "board_id", "TEXT")

        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
