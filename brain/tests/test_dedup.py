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

def _post_twice(route, tmp_db, monkeypatch, body):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    client = TestClient(app)
    return client.post(route, json=body), client.post(route, json=body)


def test_ingest_note_deduplicates(tmp_db, mock_ollama, monkeypatch):
    """v3: dedup lives in ingest, so every route gets it. A second POST of the
    same content+project returns the first id with duplicate=true."""
    first, second = _post_twice("/ingest/note", tmp_db, monkeypatch,
                                {"content": "unique content abc", "project": "myproj"})
    assert first.status_code == 201 and first.json()["duplicate"] is False
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]


def test_ingest_session_deduplicates(tmp_db, mock_ollama, monkeypatch):
    first, second = _post_twice("/ingest/session", tmp_db, monkeypatch,
                                {"content": "session log xyz", "project": "proj"})
    assert first.status_code == 201
    assert second.status_code == 200 and second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]


def test_archived_copy_is_not_a_duplicate(tmp_db, mock_ollama, monkeypatch):
    """Only an ACTIVE memory with the same content counts as a duplicate."""
    from fastapi.testclient import TestClient
    from app.main import app
    old = MemoryEntry(content="said again", type="note", project="myproj", status="archived")
    add_memory(old, db_path=tmp_db)
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    r = TestClient(app).post("/ingest/note", json={"content": "said again", "project": "myproj"})
    assert r.status_code == 201 and r.json()["id"] != old.id


def test_ingest_note_not_duplicate_calls_ingest(tmp_db, mock_ollama):
    """POST /ingest/note with new content should proceed to ingest pipeline."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models import MemoryEntry

    with patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
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
