"""SQLite connection setup.

The production database lives on an **NFS-backed PVC** on the rpi-k8s cluster.
That single fact drives everything here:

- **WAL does not work on NFS.** It's SQLite's usual default and the usual advice,
  and on a network filesystem it fails by corrupting rather than erroring. Under
  ``INTAKE_SQLITE_NFS=1`` this module uses ``journal_mode=TRUNCATE`` and
  ``synchronous=FULL`` instead.
- **Exactly one writer.** The Deployment stays at ``replicas: 1`` with
  ``strategy: Recreate``. Nothing here can enforce that, but it's the assumption
  the pragma choice is built on.

Local development keeps WAL, which is faster and perfectly safe on a real disk.
The env var name mirrors recipe-box's ``RCM_SQLITE_NFS`` so the two apps on the
cluster read the same way.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

NFS_ENV_VAR = "INTAKE_SQLITE_NFS"


def _on_nfs() -> bool:
    return os.environ.get(NFS_ENV_VAR, "") not in ("", "0")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a configured connection to the intake database.

    Callers get rows accessible by column name and foreign keys enforced, which
    SQLite otherwise leaves off.
    """
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    if _on_nfs():
        # TRUNCATE keeps the rollback journal as a plain file that's truncated
        # on commit — no shared-memory index, which is the part WAL needs and
        # NFS can't provide reliably.
        conn.execute("PRAGMA journal_mode = TRUNCATE")
        conn.execute("PRAGMA synchronous = FULL")
    else:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

    conn.execute("PRAGMA foreign_keys = ON")
    return conn
