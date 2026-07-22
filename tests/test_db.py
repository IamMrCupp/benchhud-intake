"""Connection pragmas are what they claim to be.

These assertions read the pragmas back rather than trusting that setting them
worked. `journal_mode` in particular silently refuses some transitions and
returns whatever mode it actually landed in — a test that only executes the
PRAGMA statement would happily pass against a database in the wrong mode.

Getting this wrong isn't a crash, it's corruption on NFS. Hence the paranoia.
"""

import sqlite3

import pytest

from intake.db import connect


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "intake.db"


def _pragma(conn: sqlite3.Connection, name: str):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_nfs_mode_uses_truncate_journal(db_path, monkeypatch) -> None:
    monkeypatch.setenv("INTAKE_SQLITE_NFS", "1")

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") == "truncate"


def test_nfs_mode_uses_full_synchronous(db_path, monkeypatch) -> None:
    """FULL is 2. Anything less risks a torn write surviving a power cut."""
    monkeypatch.setenv("INTAKE_SQLITE_NFS", "1")

    with connect(db_path) as conn:
        assert _pragma(conn, "synchronous") == 2


def test_local_mode_uses_wal(db_path, monkeypatch) -> None:
    """Without the flag this is a dev box, where WAL is faster and safe."""
    monkeypatch.delenv("INTAKE_SQLITE_NFS", raising=False)

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") == "wal"


def test_never_wal_in_nfs_mode(db_path, monkeypatch) -> None:
    """The specific failure this whole module exists to prevent."""
    monkeypatch.setenv("INTAKE_SQLITE_NFS", "1")

    with connect(db_path) as conn:
        assert _pragma(conn, "journal_mode") != "wal"


@pytest.mark.parametrize("nfs", ["1", None])
def test_foreign_keys_enforced_in_both_modes(db_path, monkeypatch, nfs) -> None:
    """SQLite ships with foreign keys OFF, which surprises everyone once."""
    if nfs is None:
        monkeypatch.delenv("INTAKE_SQLITE_NFS", raising=False)
    else:
        monkeypatch.setenv("INTAKE_SQLITE_NFS", nfs)

    with connect(db_path) as conn:
        assert _pragma(conn, "foreign_keys") == 1


def test_rows_are_mappings(db_path, monkeypatch) -> None:
    """Column access by name, so callers don't index into tuples positionally."""
    monkeypatch.delenv("INTAKE_SQLITE_NFS", raising=False)

    with connect(db_path) as conn:
        row = conn.execute("SELECT 42 AS answer").fetchone()
        assert row["answer"] == 42
