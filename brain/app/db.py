"""One place that opens SQLite, so every connection gets the same settings.

WAL lets readers carry on while a writer commits. foreign_keys makes the
schema's ON DELETE CASCADE rules real. busy_timeout waits out a short lock
instead of failing. synchronous=NORMAL is the durable pairing for WAL.
"""
from __future__ import annotations

import sqlite3

BUSY_TIMEOUT_MS = 5000


def connect(db_path, *, vec: bool = False, readonly: bool = False,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open SQLite with journal_mode=WAL, foreign_keys=ON, busy_timeout=5000,
    synchronous=NORMAL and sqlite3.Row rows. vec=True loads sqlite-vec.
    readonly=True sets PRAGMA query_only=ON."""
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000,
                           check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if vec:
        import sqlite_vec  # deferred: only the vector backend needs it

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    if readonly:
        conn.execute("PRAGMA query_only = ON")
    return conn
