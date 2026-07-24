"""Schema v3 — board_id and ticket links, and the migration onto them.

The migration test that matters is `test_upgrade_from_v2_adds_board_id`. A fresh
database gets the new column from the CREATE, so that path proves nothing about
upgrading a database that already has customer data in it. The v2→v3 path does.
"""

import sqlite3

import pytest

from intake.db import connect
from intake.schema import SCHEMA_VERSION, initialise


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# --- fresh database ---------------------------------------------------------


def test_fresh_init_is_v3(tmp_path) -> None:
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert SCHEMA_VERSION >= 3


def test_fresh_init_has_board_id(tmp_path) -> None:
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)

        assert "board_id" in _columns(conn, "tickets")


def test_fresh_init_has_ticket_links(tmp_path) -> None:
    with connect(tmp_path / "intake.db") as conn:
        initialise(conn)

        assert "ticket_links" in _tables(conn)
        assert {"id", "year", "counter", "label", "url", "created_at"} <= _columns(
            conn, "ticket_links"
        )


def test_init_is_still_idempotent(tmp_path) -> None:
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
        initialise(conn)
        initialise(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


# --- the upgrade path -------------------------------------------------------


def _make_v2_database(path) -> None:
    """Build a database as v2 left it — tickets without board_id, no links table."""
    conn = connect(path)
    conn.executescript(
        """
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL, counter INTEGER NOT NULL,
            device TEXT NOT NULL, serial TEXT, symptoms TEXT NOT NULL DEFAULT '',
            customer_name TEXT NOT NULL, phone TEXT, email TEXT, return_address TEXT,
            accessories TEXT, condition_notes TEXT,
            status TEXT NOT NULL, outcome TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE (year, counter),
            CONSTRAINT contactable CHECK (phone IS NOT NULL OR email IS NOT NULL)
        );
        CREATE TABLE ref_counters (
            year INTEGER PRIMARY KEY, last_counter INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO tickets (year, counter, device, customer_name, phone, status, created_at, updated_at)
            VALUES (26, 1, 'CDJ-3000', 'DJ in LA', '555-0100', 'diagnosing', 'then', 'then');
        PRAGMA user_version = 2;
        """
    )
    conn.commit()
    conn.close()


def test_v2_database_starts_without_board_id(tmp_path) -> None:
    """Sanity-check the fixture: prove the pre-migration state is what we think."""
    path = tmp_path / "intake.db"
    _make_v2_database(path)

    with connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert "board_id" not in _columns(conn, "tickets")


def test_upgrade_from_v2_adds_board_id(tmp_path) -> None:
    path = tmp_path / "intake.db"
    _make_v2_database(path)

    with connect(path) as conn:
        initialise(conn)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "board_id" in _columns(conn, "tickets")
        assert "ticket_links" in _tables(conn)


def test_upgrade_preserves_existing_rows(tmp_path) -> None:
    """The whole point of an ALTER over a rebuild: the customer's ticket survives."""
    path = tmp_path / "intake.db"
    _make_v2_database(path)

    with connect(path) as conn:
        initialise(conn)

        row = conn.execute(
            "SELECT customer_name, device, status FROM tickets WHERE year=26 AND counter=1"
        ).fetchone()
        assert row["customer_name"] == "DJ in LA"
        assert row["device"] == "CDJ-3000"
        assert row["status"] == "diagnosing"
        assert conn.execute("SELECT board_id FROM tickets").fetchone()["board_id"] is None


def test_upgrade_is_idempotent(tmp_path) -> None:
    """Running the upgrade twice must not fail on 'duplicate column'."""
    path = tmp_path / "intake.db"
    _make_v2_database(path)

    with connect(path) as conn:
        initialise(conn)
        initialise(conn)  # must not raise

        assert "board_id" in _columns(conn, "tickets")


def test_ticket_links_foreign_key(tmp_path) -> None:
    """A link must reference a real ticket."""
    path = tmp_path / "intake.db"
    with connect(path) as conn:
        initialise(conn)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ticket_links (year, counter, label, url, created_at)"
                " VALUES (26, 999, 'x', 'http://x', 't')"
            )
