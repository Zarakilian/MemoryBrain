# brain/tests/test_storage_v2.py
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest
from app.models import MemoryEntry
from app.storage import init_db, add_memory, keyword_search, get_recent, archive_memory, get_project_recent_state
from app.migrations.runner import run_migrations


def _fresh_db() -> Path:
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "brain.db"
    init_db(db)
    run_migrations(db_path=db)
    return db


def _entry(project="p", type_="note", content="hello world") -> MemoryEntry:
    return MemoryEntry(content=content, type=type_, project=project,
                       timestamp=datetime.now(timezone.utc))


def test_add_memory_default_status_active():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    results = keyword_search("hello", project="p", db_path=db)
    assert len(results) == 1
    assert results[0]["status"] == "active"


def test_keyword_search_excludes_archived_by_default():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="other-id", db_path=db)
    results = keyword_search("hello", project="p", db_path=db)
    assert len(results) == 0


def test_keyword_search_include_history():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="other-id", db_path=db)
    results = keyword_search("hello", project="p", include_history=True, db_path=db)
    assert len(results) == 1
    assert results[0]["status"] == "archived"


def test_keyword_search_tag_filter():
    db = _fresh_db()
    e1 = _entry(content="tagged entry")
    e1.tags = ["sql", "investigation"]
    e2 = _entry(content="untagged entry")
    add_memory(e1, db_path=db)
    add_memory(e2, db_path=db)
    results = keyword_search("entry", project="p", tags=["sql"], db_path=db)
    assert len(results) == 1
    assert "tagged" in results[0]["content_preview"]


def test_get_recent_excludes_archived_by_default():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="other-id", db_path=db)
    results = get_recent(project="p", db_path=db)
    assert len(results) == 0


def test_archive_memory_sets_superseded_by():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="new-id", db_path=db)
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, superseded_by FROM memories WHERE id=?", (e.id,)).fetchone()
    assert row["status"] == "archived"
    assert row["superseded_by"] == "new-id"


def test_get_project_recent_state():
    db = _fresh_db()
    e = _entry(content="fixed the bug in deployment")
    e.summary = "Deployment bug fixed"
    add_memory(e, db_path=db)
    state = get_project_recent_state("p", db_path=db)
    assert "Deployment bug fixed" in state


def test_get_project_recent_state_excludes_archived():
    db = _fresh_db()
    e = _entry(content="old stale note")
    e.summary = "Old note"
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="x", db_path=db)
    state = get_project_recent_state("p", db_path=db)
    assert state == ""
