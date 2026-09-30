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
    entry = MemoryEntry(content="disk alert firing on srv01", type="note", project="api-service")
    add_memory(entry, db_path=tmp_db)
    fetched = get_memory(entry.id, db_path=tmp_db)
    assert fetched.id == entry.id
    assert fetched.content == "disk alert firing on srv01"
    assert fetched.project == "api-service"
    assert fetched.type == "note"


def test_get_memory_not_found_returns_none(tmp_db):
    result = get_memory("nonexistent-id", db_path=tmp_db)
    assert result is None


def test_keyword_search_finds_matching_entry(tmp_db):
    e1 = MemoryEntry(content="postgres query latency is slow", type="note", project="api-service")
    e2 = MemoryEntry(content="pager alert for disk space", type="note", project="api-service")
    add_memory(e1, db_path=tmp_db)
    add_memory(e2, db_path=tmp_db)
    results = keyword_search("postgres latency", db_path=tmp_db)
    ids = [r["id"] for r in results]
    assert e1.id in ids
    assert e2.id not in ids


def test_keyword_search_returns_summary_not_full_content(tmp_db):
    e = MemoryEntry(
        content="very long content " * 100,
        summary="short summary",
        type="note",
        project="api-service",
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
    e = MemoryEntry(content="recent memory about dashboards", type="note", project="api-service")
    add_memory(e, db_path=tmp_db)
    results = get_recent(db_path=tmp_db)
    assert len(results) >= 1
    assert "content_preview" in results[0]


def test_get_next_session_notes_needs_a_project(tmp_db):
    """v3: an empty project finds nothing (no cross-project fallback)."""
    p = Project(slug="memorybrain", name="MemoryBrain")
    upsert_project(p, db_path=tmp_db)
    note = MemoryEntry(
        content="Next session: test fluffy dog recall",
        type="note",
        project="memorybrain",
        tags=["next_session"],
    )
    add_memory(note, db_path=tmp_db)
    assert get_next_session_notes(project="", db_path=tmp_db) == ""
    assert "fluffy dog" in get_next_session_notes(project="memorybrain", db_path=tmp_db).lower()


def test_get_next_session_notes_stays_in_its_project(tmp_db):
    """A project without a next_session note never gets another project's note."""
    p1 = Project(slug="memorybrain", name="MemoryBrain")
    p2 = Project(slug="api-service", name="Api Service")
    upsert_project(p1, db_path=tmp_db)
    upsert_project(p2, db_path=tmp_db)  # api-service upserted last → most recently active

    note = MemoryEntry(
        content="Next session: remember the fluffy dog test",
        type="note",
        project="memorybrain",
        tags=["next_session"],
    )
    add_memory(note, db_path=tmp_db)
    assert get_next_session_notes(project="api-service", db_path=tmp_db) == ""
    assert "fluffy dog" in get_next_session_notes(project="memorybrain", db_path=tmp_db).lower()


def test_get_next_session_notes_empty_when_no_projects(tmp_db):
    result = get_next_session_notes(project="", db_path=tmp_db)
    assert result == ""


def test_keyword_search_filters_by_project(tmp_db):
    e1 = MemoryEntry(content="sales dashboard", type="note", project="api-service")
    e2 = MemoryEntry(content="sales dashboard", type="note", project="other")
    add_memory(e1, db_path=tmp_db)
    add_memory(e2, db_path=tmp_db)
    results = keyword_search("sales", project="api-service", db_path=tmp_db)
    ids = [r["id"] for r in results]
    assert e1.id in ids
    assert e2.id not in ids


def test_upsert_and_list_projects(tmp_db):
    p = Project(slug="api-service", name="Api Service Rollout")
    upsert_project(p, db_path=tmp_db)
    projects = list_projects(db_path=tmp_db)
    assert len(projects) == 1
    assert projects[0].slug == "api-service"


def test_upsert_project_updates_last_activity(tmp_db):
    p = Project(slug="api-service", name="Api Service Rollout")
    upsert_project(p, db_path=tmp_db)
    p2 = Project(slug="api-service", name="Api Service Rollout", one_liner="Updated desc")
    upsert_project(p2, db_path=tmp_db)
    projects = list_projects(db_path=tmp_db)
    assert len(projects) == 1
    assert projects[0].one_liner == "Updated desc"


def test_project_description_columns_round_trip(tmp_db):
    from datetime import datetime, timezone
    from app.models import Project
    from app.storage import upsert_project, get_project, list_projects, _connect
    upsert_project(Project(slug="wsproj", name="WS Proj",
                           last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    with _connect(tmp_db) as conn:
        conn.execute("""UPDATE projects SET description = ?, description_source = ?
                        WHERE slug = ?""", ("A reporting job.", "user", "wsproj"))
        conn.commit()
    p = get_project("wsproj", db_path=tmp_db)
    assert p.description == "A reporting job."
    assert p.description_source == "user"
    assert list_projects(db_path=tmp_db)[0].description == "A reporting job."
    # upsert_project must NOT clobber a description
    upsert_project(Project(slug="wsproj", name="WS Proj",
                           last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    assert get_project("wsproj", db_path=tmp_db).description == "A reporting job."


def test_upsert_project_never_touches_description_columns(tmp_db):
    from datetime import datetime, timezone
    from app.models import Project
    from app.storage import upsert_project, get_project, _connect
    upsert_project(Project(slug="keep", name="Keep", last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    with _connect(tmp_db) as conn:
        conn.execute("UPDATE projects SET description = 'user text', description_source = 'user', "
                     "description_updated_at = '2026-09-06T00:00:00+00:00' WHERE slug = 'keep'")
        conn.commit()
    upsert_project(Project(slug="keep", name="Keep Renamed", last_activity=datetime.now(timezone.utc),
                           description="", description_source=""), db_path=tmp_db)
    p = get_project("keep", db_path=tmp_db)
    assert p.description == "user text" and p.description_source == "user"
    with _connect(tmp_db) as conn:
        assert conn.execute("SELECT description_updated_at FROM projects WHERE slug='keep'").fetchone()[0] == \
            "2026-09-06T00:00:00+00:00"
