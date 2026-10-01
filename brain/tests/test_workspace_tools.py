import json
from datetime import datetime, timezone

import pytest

from app.models import Project
from app.storage import upsert_project
from app.workspace import store as ws
from app.mcp import tools as T


def _seed(tmp_db, monkeypatch):
    monkeypatch.setattr(T, "DB_PATH", tmp_db)
    upsert_project(Project(slug="daily-reports", name="Daily Reports",
                           last_activity=datetime.now(timezone.utc)), db_path=tmp_db)
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": True,
                       "files": [{"rel_path": "Daily Reports/TODO-DailyReports.md", "title": "TO-DO"},
                                 {"rel_path": "Daily Reports/docs/glossary.md"}], "markers": []}, db_path=tmp_db)
    ws.bind_folder("git", "Daily Reports", "daily-reports", "init", db_path=tmp_db)


def test_tool_registry_has_four_new_tools():
    for name in ("set_project_info", "get_workspace_map", "get_project_files", "get_file_context"):
        assert name in T.TOOL_NAMES and name in T._TOOL_ARGS
    assert len(T.TOOL_NAMES) == 35  # v3 adds record_correction and brain_admin


async def test_list_tools_advertises_them(monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_TOOLS", "full")  # v3 lists 15 core tools by default
    names = {t.name for t in await T.list_tools()}
    assert {"set_project_info", "get_workspace_map", "get_project_files", "get_file_context"} <= names
    assert len(names) == len(T.TOOL_NAMES)


async def test_set_project_info_and_header_in_list_projects(tmp_db, monkeypatch):
    _seed(tmp_db, monkeypatch)
    out = json.loads(await T.handle_set_project_info(
        project="daily-reports", description="Acme's reporting job for finance.",
        home_path=r"C:\work\repos\Daily Reports\docs", label="Docs", role="area"))
    assert out["identity"]["description_source"] == "tool" and out["files_under"] == 1
    listing = await T.handle_list_projects()
    assert "**Daily Reports** (daily-reports). Acme's reporting job for finance." in listing
    summary = await T.handle_get_startup_summary()
    assert "Acme's reporting job for finance" in summary and "Home: Daily Reports" in summary


async def test_get_project_files_and_file_context(tmp_db, monkeypatch):
    _seed(tmp_db, monkeypatch)
    files = json.loads(await T.handle_get_project_files(project="daily-reports", sort="path"))
    assert files["count"] == 2 and files["files"][0]["rel_path"] == "Daily Reports/TODO-DailyReports.md"
    ctx = json.loads(await T.handle_get_file_context(path="Daily Reports/TODO-DailyReports.md"))
    assert ctx["project"] == "daily-reports" and ctx["file"]["title"] == "TO-DO"
    ctx = json.loads(await T.handle_get_file_context(path=r"C:\work\repos\Daily Reports\docs\glossary.md"))
    assert ctx["file"]["rel_path"] == "Daily Reports/docs/glossary.md"
    assert "error" in json.loads(await T.handle_get_file_context(path="nope/none.md"))
    m = json.loads(await T.handle_get_workspace_map(project="daily-reports"))
    assert m["projects"][0]["file_count"] == 2


async def test_call_tool_dispatch_and_arg_validation():
    out = await T.call_tool("get_project_files", {})
    assert "Missing required argument" in out[0].text
    out = await T.call_tool("get_file_context", {"path": "x.md", "bogus": 1})
    assert "error" in json.loads(out[0].text) or "file" in json.loads(out[0].text)


async def test_identity_header_logs_and_falls_back(monkeypatch, caplog):
    import logging
    from app.workspace import identity as idn
    monkeypatch.setattr(idn, "get_identity", lambda slug, db_path: (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level(logging.WARNING):
        out = T._identity_header("some-proj")
    assert out == "**some-proj**"
    assert any("identity header failed" in r.getMessage() for r in caplog.records)


async def test_get_file_context_reraises_unrelated_sqlite_errors(monkeypatch):
    import sqlite3
    from app.workspace import store as ws
    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(ws, "resolve_abs_path", boom)
    with pytest.raises(sqlite3.OperationalError):
        await T.handle_get_file_context(path="x.md")


async def test_brief_carries_identity(tmp_db, monkeypatch, mock_ollama):
    _seed(tmp_db, monkeypatch)
    from app.workspace.identity import set_identity
    set_identity("daily-reports", db_path=tmp_db, description="Pipeline.", source="tool")
    from app.brief import build_project_brief
    pack = await build_project_brief("daily-reports", db_path=tmp_db)
    assert pack["project_description"] == "Pipeline." and pack["project_description_source"] == "tool"
    assert pack["home_folders"][0]["rel_path"] == "Daily Reports"
    assert pack["chars_used"] <= pack["char_budget"]


async def test_set_project_info_tool_reports_an_invalid_slug(tmp_db, monkeypatch):
    _seed(tmp_db, monkeypatch)
    out = json.loads(await T.handle_set_project_info(project="Daily Reports", description="x"))
    assert "error" in out and "slug" in out["error"]


async def test_get_file_context_finds_a_file_by_bare_name(tmp_db, monkeypatch):
    _seed(tmp_db, monkeypatch)
    ctx = json.loads(await T.handle_get_file_context(path="TODO-DailyReports.md"))
    assert ctx["file"]["rel_path"] == "Daily Reports/TODO-DailyReports.md" and ctx["project"] == "daily-reports"


async def test_get_file_context_lists_candidates_when_the_name_is_ambiguous(tmp_db, monkeypatch):
    _seed(tmp_db, monkeypatch)
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work\repos", "full": False,
                       "files": [{"rel_path": "Other/glossary.md"}], "markers": []}, db_path=tmp_db)
    out = json.loads(await T.handle_get_file_context(path="glossary.md"))
    assert out["error"] == "ambiguous" and {c["rel_path"] for c in out["candidates"]} == {
        "Daily Reports/docs/glossary.md", "Other/glossary.md"}
    ctx = json.loads(await T.handle_get_file_context(path="glossary.md", project="daily-reports"))
    assert ctx["file"]["rel_path"] == "Daily Reports/docs/glossary.md"
    assert "error" in json.loads(await T.handle_get_file_context(path="nope.md"))
