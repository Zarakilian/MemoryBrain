"""v2.2 context-bank: briefs, pins, conflicts, write policy, migrations."""
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.models import MemoryEntry, ValidationError, validate_entry
from app.storage import add_memory, init_db, decay_strengths, get_memory
from app.write_policy import apply_write_policy
from app.pins import pin_memory, unpin_memory, list_pins, pinned_ids
from app.conflicts import list_conflicts, dismiss_conflict, resolve_conflict
from app.brief import build_project_brief
from app.migrations.runner import run_migrations, MIGRATIONS_DIR


def _add(db, **kwargs):
    entry = MemoryEntry(
        content=kwargs.get("content", "hello world fact"),
        type=kwargs.get("type", "fact"),
        project=kwargs.get("project", "demo"),
        summary=kwargs.get("summary", "summary"),
        importance=kwargs.get("importance", 4),
        tags=kwargs.get("tags", []),
    )
    if "id" in kwargs:
        entry.id = kwargs["id"]
    add_memory(entry, db_path=db)
    return entry


def test_migration_005_tables(tmp_path):
    db = tmp_path / "brain.db"
    # minimal base then all migrations
    with sqlite3.connect(db) as conn:
        conn.execute(
            """CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT, summary TEXT DEFAULT '',
                type TEXT, project TEXT, tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '', importance INTEGER DEFAULT 3,
                timestamp TEXT, chroma_id TEXT DEFAULT '', content_hash TEXT DEFAULT ''
            )"""
        )
        conn.execute(
            "CREATE TABLE projects (slug TEXT PRIMARY KEY, name TEXT, "
            "last_activity TEXT, one_liner TEXT DEFAULT '')"
        )
        conn.commit()
    run_migrations(db_path=db)
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        migs = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
    assert "project_pins" in tables
    assert "project_policy" in tables
    assert "retrieval_events" in tables
    assert "005_context_bank.sql" in migs
    assert len(migs) == len(list(MIGRATIONS_DIR.glob("*.sql")))


def test_write_policy_rejects_huge_fact():
    entry = MemoryEntry(content="x" * 5000, type="fact", project="demo")
    with pytest.raises(ValidationError):
        apply_write_policy(entry)


def test_write_policy_auto_tags_decision():
    entry = MemoryEntry(content="Ship on Fridays only after green CI.",
                        type="decision", project="demo")
    warnings = apply_write_policy(entry)
    assert "decision" in entry.tags
    assert entry.importance == 4
    assert warnings == []


def test_write_policy_open_loop_limit():
    entry = MemoryEntry(content="y" * 900, type="open_loop", project="demo")
    with pytest.raises(ValidationError):
        apply_write_policy(entry)


def test_validate_new_types():
    for t in ("decision", "open_loop"):
        e = MemoryEntry(content="short truth", type=t, project="demo")
        validate_entry(e)


def test_pins_roundtrip(tmp_db):
    e = _add(tmp_db, content="Live install is C:/Users/Miguel/memorybrain",
             type="fact", project="memorybrain", id="pin-1")
    r = pin_memory("memorybrain", e.id, kind="truth", label="live path",
                   priority=10, db_path=tmp_db)
    assert r.get("pinned") is True
    pins = list_pins("memorybrain", db_path=tmp_db)
    assert len(pins) == 1
    assert pins[0]["memory_id"] == e.id
    assert e.id in pinned_ids("memorybrain", db_path=tmp_db)
    u = unpin_memory("memorybrain", e.id, db_path=tmp_db)
    assert u.get("unpinned") is True
    assert list_pins("memorybrain", db_path=tmp_db) == []


def test_pin_rejects_wrong_project(tmp_db):
    e = _add(tmp_db, project="alpha", id="a1")
    r = pin_memory("beta", e.id, db_path=tmp_db)
    assert "error" in r


def test_pin_rejects_missing(tmp_db):
    r = pin_memory("demo", "no-such", db_path=tmp_db)
    assert "error" in r


def test_decay_skips_pins(tmp_db):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    pinned = _add(tmp_db, content="pinned truth", project="demo", id="p1")
    free = _add(tmp_db, content="free memory", project="demo", id="f1")
    with sqlite3.connect(tmp_db) as conn:
        conn.execute(
            "UPDATE memories SET strength = 1.0, last_recalled = ?, timestamp = ?",
            (old, old),
        )
        # ensure columns exist from migration
        conn.commit()
    pin_memory("demo", pinned.id, db_path=tmp_db)
    n = decay_strengths(idle_days=14, factor=0.5, db_path=tmp_db)
    assert n >= 1
    with sqlite3.connect(tmp_db) as conn:
        conn.row_factory = sqlite3.Row
        ps = conn.execute("SELECT strength FROM memories WHERE id='p1'").fetchone()["strength"]
        fs = conn.execute("SELECT strength FROM memories WHERE id='f1'").fetchone()["strength"]
    assert ps == 1.0  # pinned unchanged
    assert fs < 1.0   # free decayed


def _seed_conflict(db, a_id="c-a", b_id="c-b", project="demo"):
    _add(db, content="API key is required on all routes", id=a_id,
         type="fact", project=project, summary="auth required")
    _add(db, content="API key is optional on loopback routes", id=b_id,
         type="fact", project=project, summary="auth optional loopback")
    lo, hi = min(a_id, b_id), max(a_id, b_id)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db) as conn:
        # memory_links may need to exist (migration 003/004)
        conn.execute(
            """INSERT OR REPLACE INTO memory_links
               (src_id, dst_id, kind, weight, directed, meta, created_at)
               VALUES (?, ?, 'conflicts_with', 0.85, 0, ?, ?)""",
            (lo, hi, json.dumps({"flagged_at": now}), now),
        )
        conn.commit()
    return a_id, b_id


def test_list_and_dismiss_conflict(tmp_db):
    a, b = _seed_conflict(tmp_db)
    listed = list_conflicts(project="demo", db_path=tmp_db)
    assert listed["total"] == 1
    d = dismiss_conflict(a, b, db_path=tmp_db)
    assert "dismissed" in d
    listed2 = list_conflicts(project="demo", db_path=tmp_db)
    assert listed2["total"] == 0


def test_resolve_conflict_archives_loser(tmp_db):
    a, b = _seed_conflict(tmp_db, a_id="w1", b_id="l1")
    r = resolve_conflict(winner_id=a, loser_id=b, db_path=tmp_db)
    assert r.get("winner") == a
    loser = get_memory(b, db_path=tmp_db)
    assert loser.status == "archived"
    assert loser.superseded_by == a


@pytest.mark.asyncio
async def test_project_brief_includes_pins_and_facts(tmp_db, mock_ollama):
    e = _add(tmp_db, content="Port is 7741", type="fact", project="memorybrain",
             summary="Port 7741", id="m-port")
    pin_memory("memorybrain", e.id, kind="truth", label="port", db_path=tmp_db)
    _add(tmp_db, content="Use streamable HTTP for Grok", type="decision",
         project="memorybrain", summary="Grok uses /mcp", id="m-dec")
    _add(tmp_db, content="Bump VERSION file next", type="open_loop",
         project="memorybrain", summary="Bump VERSION", id="m-loop")
    pack = await build_project_brief("memorybrain", db_path=tmp_db, max_chars=4000)
    assert pack["project"] == "memorybrain"
    assert any(p["memory_id"] == "m-port" for p in pack["pins"])
    assert any(f["id"] == "m-dec" for f in pack["facts_and_decisions"])
    assert any(o["id"] == "m-loop" for o in pack["open_loops"])
    assert pack["chars_used"] <= pack["char_budget"]
    assert "protocol_hint" in pack


@pytest.mark.asyncio
async def test_project_brief_unknown_project(tmp_db):
    pack = await build_project_brief("never-seen-xyz", db_path=tmp_db)
    assert pack["project"] == "never-seen-xyz"
    assert pack["pins"] == []
    assert pack["warnings"]


@pytest.mark.asyncio
async def test_mcp_list_tools_count():
    from app.mcp.tools import list_tools, TOOL_NAMES
    tools = await list_tools()
    names = [t.name for t in tools]
    assert len(names) == 17
    assert set(names) == set(TOOL_NAMES)
    assert "get_project_brief" in names
    assert "pin_memory" in names
    assert "resolve_conflict" in names


@pytest.mark.asyncio
async def test_mcp_pin_and_brief_handlers(tmp_db, mock_ollama, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    monkeypatch.setattr("app.storage.DB_PATH", tmp_db)
    monkeypatch.setattr("app.pins.DB_PATH", tmp_db)
    monkeypatch.setattr("app.brief.DB_PATH", tmp_db)
    from app.mcp import tools as t
    e = _add(tmp_db, content="stdio for Codex", type="fact",
             project="memorybrain", id="mcp-1", summary="Codex stdio")
    pin_raw = await t.handle_pin_memory("memorybrain", e.id, kind="truth")
    assert json.loads(pin_raw)["pinned"] is True
    brief_raw = await t.handle_get_project_brief("memorybrain")
    pack = json.loads(brief_raw)
    assert pack["project"] == "memorybrain"
    assert any(p["memory_id"] == "mcp-1" for p in pack["pins"])
