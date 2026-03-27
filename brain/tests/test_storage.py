# tests/test_storage.py
import pytest
from datetime import datetime
from app.models import MemoryEntry, Project
from app.storage import (
    add_memory, get_memory, keyword_search,
    upsert_project, get_project, list_projects,
    init_db,
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
    assert "content" not in results[0]  # full content NOT in search results


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
