"""Tests for M7: FTS5 UPDATE and DELETE triggers."""
import sqlite3
import pytest
from app.storage import init_db, add_memory, _connect
from app.models import MemoryEntry


def _fts_search(db_path, query):
    """Direct FTS5 search returning matching memory IDs."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT m.id FROM memories_fts JOIN memories m ON memories_fts.rowid = m.rowid WHERE memories_fts MATCH ?",
        (f'"{query}"',),
    ).fetchall()
    conn.close()
    return [r["id"] for r in rows]


def test_fts_insert_trigger_works(tmp_db):
    """Existing INSERT trigger: new memory should be findable in FTS5."""
    entry = MemoryEntry(content="fts insert test", type="note", project="test")
    add_memory(entry, db_path=tmp_db)
    assert entry.id in _fts_search(tmp_db, "fts insert test")


def test_fts_delete_trigger(tmp_db):
    """After deleting a memory row, FTS5 should no longer return it."""
    entry = MemoryEntry(content="delete me fts", type="note", project="test")
    add_memory(entry, db_path=tmp_db)
    assert entry.id in _fts_search(tmp_db, "delete me fts")

    # Delete the row
    conn = sqlite3.connect(tmp_db)
    conn.execute("DELETE FROM memories WHERE id = ?", (entry.id,))
    conn.commit()
    conn.close()

    assert entry.id not in _fts_search(tmp_db, "delete me fts")


def test_fts_update_trigger(tmp_db):
    """After updating content, FTS5 should reflect the new content, not old."""
    entry = MemoryEntry(content="old unique content xyzzy", type="note", project="test")
    add_memory(entry, db_path=tmp_db)
    assert entry.id in _fts_search(tmp_db, "xyzzy")

    # Update the content
    conn = sqlite3.connect(tmp_db)
    conn.execute("UPDATE memories SET content = 'new unique content plugh' WHERE id = ?", (entry.id,))
    conn.commit()
    conn.close()

    # Old content should not match
    assert entry.id not in _fts_search(tmp_db, "xyzzy")
    # New content should match
    assert entry.id in _fts_search(tmp_db, "plugh")
