"""Every SQLite connection gets the same safe settings (brain/app/db.py)."""
import sqlite3

import pytest

from app.db import connect


def _pragma(conn, name):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_connect_turns_on_wal_foreign_keys_and_busy_timeout(tmp_path):
    conn = connect(tmp_path / "b.db")
    try:
        assert _pragma(conn, "journal_mode") == "wal"
        assert _pragma(conn, "foreign_keys") == 1
        assert _pragma(conn, "busy_timeout") == 5000
        assert _pragma(conn, "synchronous") == 1  # NORMAL
    finally:
        conn.close()


def test_connect_returns_rows_by_name(tmp_path):
    conn = connect(tmp_path / "b.db")
    try:
        row = conn.execute("SELECT 7 AS seven").fetchone()
        assert row["seven"] == 7
    finally:
        conn.close()


def test_vec_connection_loads_sqlite_vec(tmp_path):
    conn = connect(tmp_path / "b.db", vec=True)
    try:
        assert conn.execute("SELECT vec_version()").fetchone()[0]
    finally:
        conn.close()


def test_readonly_connection_rejects_writes(tmp_path):
    path = tmp_path / "b.db"
    rw = connect(path)
    rw.execute("CREATE TABLE t (x INTEGER)")
    rw.commit()
    rw.close()
    ro = connect(path, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES (1)")
        assert ro.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0
    finally:
        ro.close()


def test_deleting_a_memory_cascades_to_its_vector(tmp_db):
    from app.models import MemoryEntry
    from app.storage import add_memory, delete_memory
    from app.vector import vec_add

    entry = MemoryEntry(content="cascade me", type="note", project="acme")
    add_memory(entry, db_path=tmp_db)
    vec_add(entry.id, [0.1] * 8, {}, db_path=tmp_db)
    delete_memory(entry.id, db_path=tmp_db)
    conn = connect(tmp_db)
    try:
        left = conn.execute("SELECT COUNT(*) FROM vec_memories WHERE memory_id = ?",
                            (entry.id,)).fetchone()[0]
    finally:
        conn.close()
    assert left == 0


def test_vector_for_a_missing_memory_is_refused(tmp_db):
    from app.vector import vec_add

    with pytest.raises(sqlite3.IntegrityError):
        vec_add("no-such-memory", [0.1] * 8, {}, db_path=tmp_db)
