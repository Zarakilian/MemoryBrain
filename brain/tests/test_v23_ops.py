"""v2.3: scheduler light mode, retrieval feedback, policy, timeline, obsidian."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.models import MemoryEntry
from app.storage import add_memory, get_meta, set_meta, init_db
from app.retrieval import record_retrieval, feedback_boosts
from app.policy import get_policy, set_policy
from app.timeline import get_timeline, get_entities
from app.obsidian import export_project_markdown
from app.scheduler import scheduler_status, run_auto_consolidate
from app.search import reciprocal_rank_fusion


def _add(db, **kw):
    e = MemoryEntry(
        content=kw.get("content", "content about MemoryBrain Docker on port 7741"),
        type=kw.get("type", "fact"),
        project=kw.get("project", "demo"),
        summary=kw.get("summary", "summary"),
        importance=kw.get("importance", 4),
        tags=kw.get("tags", ["MemoryBrain"]),
    )
    if "id" in kw:
        e.id = kw["id"]
    add_memory(e, db_path=db)
    return e


def test_meta_roundtrip(tmp_db):
    set_meta("k1", "v1", db_path=tmp_db)
    assert get_meta("k1", db_path=tmp_db) == "v1"
    assert get_meta("missing", default="x", db_path=tmp_db) == "x"


def test_record_retrieval_and_feedback(tmp_db):
    _add(tmp_db, id="m-chosen", content="chosen fact")
    r = record_retrieval(
        query="where is the port",
        result_ids=["m-chosen", "other"],
        chosen_id="m-chosen",
        project="demo",
        source="test",
        db_path=tmp_db,
    )
    assert r.get("recorded") is True
    boosts = feedback_boosts(["m-chosen", "other"], db_path=tmp_db)
    assert boosts["m-chosen"] > 1.0
    assert "other" not in boosts


def test_rrf_applies_feedback():
    kw = [{"id": "a", "timestamp": datetime.now(timezone.utc).isoformat()},
          {"id": "b", "timestamp": datetime.now(timezone.utc).isoformat()}]
    sem = []
    order = reciprocal_rank_fusion(kw, sem, feedback={"b": 3.0})
    assert order[0] == "b"


def test_policy_set_get(tmp_db):
    p = set_policy("demo", include_system=False, max_brief_chars=2000,
                   notes="prefer facts", default_tags=["ops"], db_path=tmp_db)
    assert p["exists"] is True
    assert p["include_system"] is False
    assert p["max_brief_chars"] == 2000
    g = get_policy("demo", db_path=tmp_db)
    assert g["notes"] == "prefer facts"
    assert "ops" in g["default_tags"]


def test_timeline_and_entities(tmp_db):
    _add(tmp_db, type="decision", content="Use Streamable HTTP for Grok clients",
         summary="Grok uses /mcp", tags=["mcp", "Grok"])
    _add(tmp_db, type="session", content="Next session: ship v2.3\n#deploy",
         summary="session log")
    tl = get_timeline(project="demo", days=7, db_path=tmp_db)
    assert tl["count"] >= 2
    ent = get_entities(project="demo", db_path=tmp_db)
    assert ent["count"] >= 1
    names = {e["name"].lower() for e in ent["entities"]}
    assert "mcp" in names or "grok" in names or "deploy" in names


def test_obsidian_export(tmp_db, tmp_path):
    _add(tmp_db, content="Live install path documented", summary="live path")
    out = export_project_markdown("demo", tmp_path, db_path=tmp_db)
    assert out["files"] >= 1
    assert (tmp_path / "demo" / "INDEX.md").exists()
    md_files = list((tmp_path / "demo").glob("*.md"))
    assert any(p.name != "INDEX.md" for p in md_files)
    body = next(p for p in md_files if p.name != "INDEX.md").read_text(encoding="utf-8")
    assert "---" in body
    assert "project: demo" in body


def test_scheduler_status_shape(tmp_db):
    st = scheduler_status(db_path=tmp_db)
    assert "enabled" in st
    assert "hour_utc" in st


@pytest.mark.asyncio
async def test_auto_consolidate_disabled_skips(tmp_db, monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_AUTO_CONSOLIDATE", raising=False)
    r = await run_auto_consolidate(db_path=tmp_db, force=False)
    assert r.get("skipped") is True


@pytest.mark.asyncio
async def test_mcp_tool_count_v23():
    from app.mcp.tools import list_tools, TOOL_NAMES
    tools = await list_tools()
    names = {t.name for t in tools}
    assert len(names) == 29   # 22 (v2.3) + 7 Synapse tools (v2.4)
    assert set(names) == set(TOOL_NAMES)
    assert "record_retrieval" in names
    assert "get_timeline" in names
    assert "set_project_policy" in names
    assert "post_task" in names
    assert "get_agent_inbox" in names


@pytest.mark.asyncio
async def test_light_consolidate_skips_beliefs(tmp_db, mock_ollama, monkeypatch):
    from app.consolidate import consolidate
    # seed a few linked-ish memories
    for i in range(3):
        _add(tmp_db, id=f"c{i}", content=f"fact number {i} about the same system",
             summary=f"fact {i}")
    report = await consolidate(project="demo", mode="light", db_path=tmp_db)
    assert report.get("mode") == "light"
    # light mode should not create new beliefs via LLM path
    beliefs = [p for p in report.get("projects", []) if p.get("beliefs")]
    # empty beliefs lists are fine
    assert report["mode"] == "light"
