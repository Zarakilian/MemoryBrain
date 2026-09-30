from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from app.models import MemoryEntry, Project
from app.storage import add_memory, upsert_project, get_project
from app.workspace import store as ws
from app.workspace import identity as idn


def _proj(tmp_db, slug="daily-reports", name="Daily Reports"):
    upsert_project(Project(slug=slug, name=name, last_activity=datetime.now(timezone.utc)), db_path=tmp_db)


def test_set_identity_name_description_and_home_path(tmp_db):
    _proj(tmp_db)
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [{"rel_path": "Daily Reports/Tools/ReportFlow/a.md"},
                                 {"rel_path": "Daily Reports/Tools/ReportFlow/b.ps1"},
                                 {"rel_path": "Daily Reports/docs/c.md"}], "markers": []}, db_path=tmp_db)
    out = idn.set_identity("daily-reports", db_path=tmp_db, description="Acme's nightly reporting job.",
                           home_path=r"C:\work\repos\Daily Reports\Tools\ReportFlow", label="ReportFlow working folder",
                           role="area")
    assert out["identity"]["description"] == "Acme's nightly reporting job."
    assert out["identity"]["description_source"] == "tool"
    assert out["binding"]["row"]["rel_path"] == "Daily Reports/Tools/ReportFlow" and out["binding"]["row"]["how"] == "tool"
    assert out["files_under"] == 2
    line = idn.header_line(out["identity"])
    assert line.startswith("**Daily Reports** (daily-reports). Acme's nightly reporting job.")
    assert "Home: Daily Reports/Tools/ReportFlow" in line


def test_description_source_precedence(tmp_db):
    _proj(tmp_db)
    idn.set_identity("daily-reports", db_path=tmp_db, description="auto text", source="auto")
    idn.set_identity("daily-reports", db_path=tmp_db, description="tool text", source="tool")
    assert get_project("daily-reports", db_path=tmp_db).description == "tool text"
    idn.set_identity("daily-reports", db_path=tmp_db, description="user text", source="user")
    out = idn.set_identity("daily-reports", db_path=tmp_db, description="later tool text", source="tool")
    assert out["skipped_description"] is True
    assert get_project("daily-reports", db_path=tmp_db).description == "user text"
    # user can always overwrite user
    idn.set_identity("daily-reports", db_path=tmp_db, description="user text 2", source="user")
    assert get_project("daily-reports", db_path=tmp_db).description == "user text 2"


def test_set_identity_creates_missing_project_row_and_truncates(tmp_db):
    out = idn.set_identity("brand-new", db_path=tmp_db, name="Brand New", description="x" * 900, source="tool")
    assert out["identity"]["name"] == "Brand New" and len(out["identity"]["description"]) == idn.MAX_DESCRIPTION


async def test_maybe_draft_description_only_when_empty_and_corpus_exists(tmp_db):
    _proj(tmp_db)
    assert await idn.maybe_draft_description("daily-reports", db_path=tmp_db) is False  # no corpus
    for i in range(3):
        add_memory(MemoryEntry(content=f"Report job fact number {i} about the exporter and its schedule " * 4,
                               summary=f"fact {i}", type="fact", project="daily-reports", importance=4),
                   db_path=tmp_db)
    with patch("app.workspace.identity.summarise", new_callable=AsyncMock) as m:
        m.return_value = "Daily Reports builds the nightly summary. It is Acme's reporting job."
        assert await idn.maybe_draft_description("daily-reports", db_path=tmp_db) is True
        p = get_project("daily-reports", db_path=tmp_db)
        assert p.description_source == "auto" and p.description.startswith("Daily Reports builds")
        # already described -> no second LLM call
        assert await idn.maybe_draft_description("daily-reports", db_path=tmp_db) is False
        assert m.await_count == 1
    # user text is never replaced by a draft, even with force
    idn.set_identity("daily-reports", db_path=tmp_db, description="user text", source="user")
    with patch("app.workspace.identity.summarise", new_callable=AsyncMock) as m:
        assert await idn.maybe_draft_description("daily-reports", db_path=tmp_db, force=True) is False
        assert m.await_count == 0


async def test_backfill_descriptions(tmp_db):
    _proj(tmp_db, "a-proj", "A"); _proj(tmp_db, "b-proj", "B")
    for i in range(3):
        add_memory(MemoryEntry(content="Something substantial about the A project and its purpose " * 4,
                               summary="s", type="fact", project="a-proj"), db_path=tmp_db)
    with patch("app.workspace.identity.summarise", new_callable=AsyncMock) as m:
        m.return_value = "A does things."
        rep = await idn.backfill_descriptions(db_path=tmp_db)
    assert rep["drafted"] == ["a-proj"] and "b-proj" in rep["skipped"]


def test_files_under_counts_beyond_the_list_limit(tmp_db):
    _proj(tmp_db)
    ws.upsert_root("git", "m", r"C:\work\repos", db_path=tmp_db)
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [{"rel_path": f"Big/f{i}.md"} for i in range(520)], "markers": []}, db_path=tmp_db)
    out = idn.set_identity("daily-reports", db_path=tmp_db, home_path=r"C:\work\repos\Big")
    assert out["files_under"] == 520


async def test_light_consolidation_drafts_description(tmp_db, mock_ollama, monkeypatch):
    import app.consolidate as cons
    monkeypatch.setattr(cons, "DB_PATH", tmp_db)
    _proj(tmp_db)
    for i in range(3):
        add_memory(MemoryEntry(content="Report job fact about the exporter and its schedule and outputs " * 4,
                               summary=f"fact {i}", type="fact", project="daily-reports", importance=4),
                   db_path=tmp_db)
    with patch("app.workspace.identity.summarise", new_callable=AsyncMock) as m:
        m.return_value = "Daily Reports is the reporting job."
        report = await cons.consolidate(project="daily-reports", mode="light", db_path=tmp_db)
    assert report["projects"][0]["description_drafted"] is True
    assert get_project("daily-reports", db_path=tmp_db).description == "Daily Reports is the reporting job."


def test_set_identity_rejects_an_invalid_slug(tmp_db):
    import pytest
    from app.storage import get_project
    with pytest.raises(ValueError):
        idn.set_identity("Daily Reports", db_path=tmp_db, description="x")
    assert get_project("Daily Reports", db_path=tmp_db) is None
