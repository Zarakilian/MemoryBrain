# tests/test_storage.py
import pytest
from datetime import datetime
from app.models import MemoryEntry, Project
from app.storage import (
    add_memory, get_memory, keyword_search, get_recent,
    upsert_project, get_project, list_projects,
    get_next_session_notes, init_db,
)


def test_add_and_get_memory(tmp_db):
    entry = MemoryEntry(content="disk alert firing on iom3110", type="note", project="monitoring")
    add_memory(entry, db_path=tmp_db)
    fetched = get_memory(entry.id, db_path=tmp_db)
    assert fetched.id == entry.id
    assert fetched.content == "disk alert firing on iom3110"
    assert fetched.project == "monitoring"
    assert fetched.type == "note"


def test_get_memory_not_found_returns_none(tmp_db):
    result = get_memory("nonexistent-id", db_path=tmp_db)
    assert result is None


def test_keyword_search_finds_matching_entry(tmp_db):
    e1 = MemoryEntry(content="clickhouse query latency is slow", type="note", project="monitoring")
    e2 = MemoryEntry(content="pagerduty alert for disk space", type="note", project="monitoring")
    add_memory(e1, db_path=tmp_db)
    add_memory(e2, db_path=tmp_db)
    results = keyword_search("clickhouse latency", db_path=tmp_db)
    ids = [r["id"] for r in results]
    assert e1.id in ids
    assert e2.id not in ids


def test_keyword_search_returns_summary_not_full_content(tmp_db):
    e = MemoryEntry(
        content="very long content " * 100,
        summary="short summary",
        type="note",
        project="monitoring",
    )
    add_memory(e, db_path=tmp_db)
    results = keyword_search("very long content", db_path=tmp_db)
    assert results[0]["summary"] == "short summary"
    assert "content" not in results[0]  # full content column NOT in results
    assert "content_preview" in results[0]  # but preview IS included
    assert results[0]["content_preview"] == ("very long content " * 100)[:200]


def test_keyword_search_content_preview_contains_keywords(tmp_db):
    """FTS match on raw content surfaces keyword via content_preview even if summary is bad."""
    e = MemoryEntry(
        content="The last thing I said was 'Fluffy dog'. Test of cross-session recall.",
        summary="no meaningful information to summarize",
        type="note",
        project="test",
    )
    add_memory(e, db_path=tmp_db)
    results = keyword_search("fluffy", db_path=tmp_db)
    assert len(results) == 1
    assert "Fluffy dog" in results[0]["content_preview"]


def test_get_recent_includes_content_preview(tmp_db):
    e = MemoryEntry(content="recent memory about grafana", type="note", project="monitoring")
    add_memory(e, db_path=tmp_db)
    results = get_recent(db_path=tmp_db)
    assert len(results) >= 1
    assert "content_preview" in results[0]


def test_get_next_session_notes_fallback_finds_any_project(tmp_db):
    """When project is empty, finds the most recent next_session note across all projects."""
    p = Project(slug="memorybrain", name="MemoryBrain")
    upsert_project(p, db_path=tmp_db)
    note = MemoryEntry(
        content="Next session: test fluffy dog recall",
        type="note",
        project="memorybrain",
        tags=["next_session"],
    )
    add_memory(note, db_path=tmp_db)
    result = get_next_session_notes(project="", db_path=tmp_db)
    assert "fluffy dog" in result.lower()


def test_get_next_session_notes_crosses_projects(tmp_db):
    """Most recent next_session note surfaces even when another project is more recently active."""
    p1 = Project(slug="memorybrain", name="MemoryBrain")
    p2 = Project(slug="monitoring", name="Monitoring")
    upsert_project(p1, db_path=tmp_db)
    upsert_project(p2, db_path=tmp_db)  # monitoring upserted last → most recently active

    note = MemoryEntry(
        content="Next session: remember the fluffy dog test",
        type="note",
        project="memorybrain",
        tags=["next_session"],
    )
    add_memory(note, db_path=tmp_db)
    # monitoring has no next_session notes — should still find memorybrain's note
    result = get_next_session_notes(project="", db_path=tmp_db)
    assert "fluffy dog" in result.lower()


def test_get_next_session_notes_empty_when_no_projects(tmp_db):
    result = get_next_session_notes(project="", db_path=tmp_db)
    assert result == ""


def test_keyword_search_filters_by_project(tmp_db):
    e1 = MemoryEntry(content="grafana dashboard", type="note", project="monitoring")
    e2 = MemoryEntry(content="grafana dashboard", type="note", project="other")
    add_memory(e1, db_path=tmp_db)
    add_memory(e2, db_path=tmp_db)
    results = keyword_search("grafana", project="monitoring", db_path=tmp_db)
    ids = [r["id"] for r in results]
    assert e1.id in ids
    assert e2.id not in ids


def test_upsert_and_list_projects(tmp_db):
    p = Project(slug="monitoring", name="Monitoring Migration")
    upsert_project(p, db_path=tmp_db)
    projects = list_projects(db_path=tmp_db)
    assert len(projects) == 1
    assert projects[0].slug == "monitoring"


def test_upsert_project_updates_last_activity(tmp_db):
    p = Project(slug="monitoring", name="Monitoring Migration")
    upsert_project(p, db_path=tmp_db)
    p2 = Project(slug="monitoring", name="Monitoring Migration", one_liner="Updated desc")
    upsert_project(p2, db_path=tmp_db)
    projects = list_projects(db_path=tmp_db)
    assert len(projects) == 1
    assert projects[0].one_liner == "Updated desc"
