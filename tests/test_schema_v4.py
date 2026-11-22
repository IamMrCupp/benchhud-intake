"""Schema v4 — the requests table, and the migration onto it.

As with v3, the test that matters is the upgrade: a fresh database gets the
table from the CREATE, which says nothing about a v3 database that already has
customers' tickets in it.
"""

import sqlite3

import pytest

from intake.db import connect
from intake.schema import SCHEMA_VERSION, initialise
from intake.store import TicketStore
from intake.web_requests import RequestStore, validate

FIELDS = validate(
    {
        "customer_name": "Sam Example",
        "email": "sam@example.com",
        "device": "Game Boy Advance",
        "symptoms": "No sound.",
        "handoff": "drop_off",
    }
)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


@pytest.fixture
def conn(tmp_path):
    with connect(tmp_path / "intake.db") as c:
        initialise(c)
        yield c


def _insert(conn: sqlite3.Connection, **overrides) -> None:
    """Insert a request straight over SQL, past the store and the validator."""
    row = {
        "public_id": "opaque-1",
        "customer_name": "Sam Example",
        "phone": None,
        "email": "sam@example.com",
        "device": "Game Boy Advance",
        "symptoms": "No sound.",
        "handoff": "drop_off",
        "state": "pending",
        "received_at": "2026-11-22T00:00:00+00:00",
    }
    row.update(overrides)
    conn.execute(
        f"INSERT INTO requests ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
        tuple(row.values()),
    )


# --- fresh database ---------------------------------------------------------


def test_fresh_init_is_v4_with_a_requests_table(conn) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert SCHEMA_VERSION >= 4
    assert "requests" in _tables(conn)


def test_init_is_still_idempotent(conn) -> None:
    RequestStore(conn).submit(FIELDS)
    initialise(conn)
    initialise(conn)

    assert RequestStore(conn).count_pending() == 1


# --- the upgrade path -------------------------------------------------------


def _make_v3_database(path) -> None:
    """A database as v3 left it: tickets, counters and links, no requests."""
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL, counter INTEGER NOT NULL,
            device TEXT NOT NULL, serial TEXT, symptoms TEXT NOT NULL DEFAULT '',
            customer_name TEXT NOT NULL, phone TEXT, email TEXT, return_address TEXT,
            accessories TEXT, condition_notes TEXT, board_id TEXT,
            status TEXT NOT NULL, outcome TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (year, counter),
            CONSTRAINT contactable CHECK (phone IS NOT NULL OR email IS NOT NULL)
        );
        CREATE TABLE ref_counters (
            year INTEGER PRIMARY KEY, last_counter INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE ticket_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT, year INTEGER NOT NULL, counter INTEGER NOT NULL,
            label TEXT NOT NULL, url TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY (year, counter) REFERENCES tickets (year, counter) ON DELETE CASCADE
        );
        INSERT INTO tickets (year, counter, device, customer_name, phone, board_id, status,
                             created_at, updated_at)
            VALUES (26, 7, 'ThinkPad X1', 'A. Customer', '555-0100', '820-00239', 'diagnosing',
                    '2026-09-20T00:00:00+00:00', '2026-09-20T00:00:00+00:00');
        INSERT INTO ref_counters (year, last_counter) VALUES (26, 7);
        INSERT INTO ticket_links (year, counter, label, url, created_at)
            VALUES (26, 7, 'Boardview', 'https://example.com/bv', '2026-09-20T00:00:00+00:00');
        PRAGMA user_version = 3;
        """
    )
    conn.commit()
    conn.close()


def test_upgrade_from_v3_adds_requests_and_keeps_every_row(tmp_path) -> None:
    path = tmp_path / "intake.db"
    _make_v3_database(path)

    with connect(path) as conn:
        assert "requests" not in _tables(conn)
        before = [tuple(r) for r in conn.execute("SELECT * FROM tickets")]

        initialise(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "requests" in _tables(conn)
        assert [tuple(r) for r in conn.execute("SELECT * FROM tickets")] == before
        assert conn.execute("SELECT COUNT(*) FROM ticket_links").fetchone()[0] == 1
        # The table was not rebuilt: same rowid, and the counter carries on.
        assert conn.execute("SELECT id FROM tickets").fetchone()[0] == 1
        store = TicketStore(conn, year=26)
        assert store.get_by_ref("26-0007").board_id == "820-00239"
        assert store.create_ticket(device="Board", customer_name="B", phone="555").ref == "26-0008"


def test_an_upgraded_database_takes_requests(tmp_path) -> None:
    path = tmp_path / "intake.db"
    _make_v3_database(path)

    with connect(path) as conn:
        initialise(conn)
        public_id, created = RequestStore(conn).submit(FIELDS)

        assert created
        assert RequestStore(conn).get(public_id).state == "pending"


# --- what the database enforces itself --------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"state": "maybe"},
        {"handoff": "courier"},
        {"customer_name": None},
        {"device": None},
        {"symptoms": None},
        {"email": None, "phone": None},
        # Pending carries no resolution time; anything else must.
        {"resolved_at": "2026-11-22T00:00:00+00:00"},
        {"state": "dismissed"},
        # Accepted means a ticket, and only accepted does.
        {"state": "accepted", "resolved_at": "2026-11-22T00:00:00+00:00"},
        {"ticket_year": 26, "ticket_counter": 1},
    ],
)
def test_the_checks_reject_a_row_the_app_would_never_write(conn, overrides) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, **overrides)


def test_an_accepted_row_may_be_a_stub_with_no_details(conn) -> None:
    _insert(
        conn,
        state="accepted",
        resolved_at="2026-11-22T00:00:00+00:00",
        ticket_year=26,
        ticket_counter=1,
        customer_name=None,
        email=None,
        device=None,
        symptoms=None,
    )

    assert RequestStore(conn).get("opaque-1").ticket_ref == "26-0001"


def test_public_id_and_idempotency_key_are_unique(conn) -> None:
    _insert(conn, idempotency_key="k")
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, public_id="opaque-2", idempotency_key="k")
    # No key at all is not a clash.
    _insert(conn, public_id="opaque-3")
    _insert(conn, public_id="opaque-4")
