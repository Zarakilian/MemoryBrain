"""Tests for content deduplication (H4)."""
import hashlib
import pytest
from unittest.mock import patch, AsyncMock

from app.models import MemoryEntry
from app.storage import add_memory, init_db


def _hash(content: str, project: str) -> str:
    return hashlib.sha256(f"{content}|{project}".encode()).hexdigest()


# ── Storage layer: content_hash column ──────────────────────────────────────

def test_content_hash_column_exists(tmp_db):
    """The memories table should have a content_hash column after init_db."""
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    cursor = conn.execute("PRAGMA table_info(memories)")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    assert "content_hash" in columns


def test_add_memory_stores_content_hash(tmp_db):
    """add_memory should compute and store a SHA-256 hash of content|project."""
    entry = MemoryEntry(content="hello world", type="note", project="test")
    add_memory(entry, db_path=tmp_db)

    import sqlite3
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT content_hash FROM memories WHERE id = ?", (entry.id,)).fetchone()
    conn.close()

    expected = _hash("hello world", "test")
    assert row["content_hash"] == expected


def test_get_memory_by_content_hash_returns_existing(tmp_db):
    """get_memory_by_content_hash should find a memory with matching hash."""
    from app.storage import get_memory_by_content_hash
    entry = MemoryEntry(content="dedup test", type="note", project="proj")
    add_memory(entry, db_path=tmp_db)

    found = get_memory_by_content_hash("dedup test", "proj", db_path=tmp_db)
    assert found is not None
    assert found.id == entry.id


def test_get_memory_by_content_hash_returns_none_when_missing(tmp_db):
    """get_memory_by_content_hash should return None for unseen content."""
    from app.storage import get_memory_by_content_hash
    found = get_memory_by_content_hash("never seen", "proj", db_path=tmp_db)
    assert found is None


# ── Ingest endpoint deduplication ───────────────────────────────────────────

def test_ingest_note_deduplicates(tmp_db, mock_ollama):
    """POST /ingest/note should return existing ID for duplicate content+project."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import MemoryEntry
    from app.storage import add_memory, get_memory_by_content_hash

    # Manually store first entry
    entry = MemoryEntry(content="unique content abc", type="note", project="myproj",
                        summary="test summary", importance=3)
    add_memory(entry, db_path=tmp_db)

    # Second POST with same content+project → dedup
    with patch("app.ingestion.manual.get_memory_by_content_hash") as mock_dedup, \
         patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_dedup.return_value = entry  # simulate finding duplicate
        client = TestClient(app)
        r = client.post("/ingest/note", json={
            "content": "unique content abc", "project": "myproj"
        })
    assert r.status_code == 200
    assert r.json()["id"] == entry.id
    assert r.json()["duplicate"] is True
    mock_ingest.assert_not_called()  # should NOT have called ingest


def test_ingest_session_deduplicates(tmp_db, mock_ollama):
    """POST /ingest/session should return existing ID for duplicate content+project."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import MemoryEntry

    entry = MemoryEntry(content="session log xyz", type="session", project="proj",
                        summary="session summary", importance=3)

    with patch("app.ingestion.session.get_memory_by_content_hash") as mock_dedup, \
         patch("app.ingestion.session.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_dedup.return_value = entry
        client = TestClient(app)
        r = client.post("/ingest/session", json={
            "content": "session log xyz", "project": "proj"
        })
    assert r.status_code == 200
    assert r.json()["duplicate"] is True
    mock_ingest.assert_not_called()


def test_ingest_note_not_duplicate_calls_ingest(tmp_db, mock_ollama):
    """POST /ingest/note with new content should proceed to ingest pipeline."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import MemoryEntry

    with patch("app.ingestion.manual.get_memory_by_content_hash", return_value=None), \
         patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = MemoryEntry(id="new-1", content="x", type="note", project="p")
        client = TestClient(app)
        r = client.post("/ingest/note", json={
            "content": "brand new content", "project": "myproj"
        })
    assert r.status_code == 201
    mock_ingest.assert_called_once()


def test_different_projects_not_considered_duplicate(tmp_db, mock_ollama):
    """Same content but different project should NOT be treated as duplicate."""
    from app.storage import get_memory_by_content_hash

    entry_a = MemoryEntry(content="same content", type="note", project="proj-a")
    entry_b = MemoryEntry(content="same content", type="note", project="proj-b")
    add_memory(entry_a, db_path=tmp_db)
    add_memory(entry_b, db_path=tmp_db)

    found_a = get_memory_by_content_hash("same content", "proj-a", db_path=tmp_db)
    found_b = get_memory_by_content_hash("same content", "proj-b", db_path=tmp_db)
    assert found_a is not None
    assert found_b is not None
    assert found_a.id != found_b.id
