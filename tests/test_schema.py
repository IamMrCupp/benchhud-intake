"""Schema initialisation and the constraints the database enforces itself.

The CHECK constraints are tested directly against SQL inserts rather than
through the store API on purpose. They exist precisely so that a future script,
a manual `sqlite3` session, or a bug in the Phase 5 API can't route around them
— so the tests have to route around the API too, or they'd be testing the wrong
layer.
"""

import sqlite3

import pytest

from intake.db import connect
from intake.schema import SCHEMA_VERSION, initialise


@pytest.fixture
def conn(tmp_path):
    with connect(tmp_path / "intake.db") as c:
        initialise(c)
        yield c


def _insert(conn: sqlite3.Connection, **overrides) -> None:
    """Insert a ticket straight over SQL, bypassing any store logic."""
    row = {
        "year": 26,
        "counter": 1,
        "device": "ThinkPad X1",
        "serial": None,
        "symptoms": "no power",
        "customer_name": "A. Customer",
        "phone": "555-0100",
        "email": None,
        "return_address": None,
        "accessories": None,
        "condition_notes": None,
        "status": "intake",
        "outcome": None,
        "created_at": "2026-07-22T00:00:00Z",
        "updated_at": "2026-07-22T00:00:00Z",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(["?"] * len(row))
    conn.execute(f"INSERT INTO tickets ({cols}) VALUES ({marks})", tuple(row.values()))


def test_initialise_creates_tickets_table(conn) -> None:
    found = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tickets'"
    ).fetchone()

    assert found is not None


def test_initialise_sets_user_version(conn) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_initialise_is_idempotent(conn) -> None:
    """Running it twice is a no-op, not an error — and keeps existing rows."""
    _insert(conn)
    conn.commit()

    initialise(conn)
    initialise(conn)

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_ref_pair_is_unique(conn) -> None:
    """B.1's atomic allocation leans on this being enforced by the database."""
    _insert(conn, year=26, counter=1)

    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, year=26, counter=1)


def test_same_counter_in_a_different_year_is_fine(conn) -> None:
    """Counters reset each January, so (25, 1) and (26, 1) must coexist."""
    _insert(conn, year=25, counter=1)
    _insert(conn, year=26, counter=1)

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 2


def test_requires_phone_or_email(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, phone=None, email=None)


@pytest.mark.parametrize(
    ("phone", "email"),
    [("555-0100", None), (None, "a@example.com"), ("555-0100", "a@example.com")],
)
def test_either_contact_alone_is_enough(conn, phone, email) -> None:
    _insert(conn, phone=phone, email=email)

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_closed_requires_an_outcome(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="closed", outcome=None)


def test_open_ticket_cannot_carry_an_outcome(conn) -> None:
    """The invariant runs both ways — an outcome means it's finished."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="repairing", outcome="repaired")


def test_closed_with_an_outcome_is_accepted(conn) -> None:
    _insert(conn, status="closed", outcome="repaired")

    assert conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_unknown_status_is_rejected(conn) -> None:
    """Guards against a typo'd status reaching the table."""
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="diagnosin")


def test_unknown_outcome_is_rejected(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, status="closed", outcome="fixed-ish")


def test_customer_name_is_required(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _insert(conn, customer_name=None)
