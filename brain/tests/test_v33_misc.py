"""3.3 odds and ends: memories are counted under the agent that wrote them, a
project with a confirmed home is not guessed a second one, the migration adds
its columns, and the core tool list still fits its budget."""
import json

import pytest

from app.db import connect
from app.exchange import agent_stats, attribute_memory
from app.mcp import tools as T
from app.models import MemoryEntry, Project
from app.storage import add_memory, upsert_project
from app.workspace import resolve as rs
from app.workspace import store as ws


@pytest.mark.parametrize("writer,source,agent", [
    ("claude-code", "log-everything", "claude"),
    ("mcp:codex-cli", "", "codex"),
    ("grok-cli", "support-investigation", "grok"),
    ("", "claude", "claude"),
    ("", "log-everything", "other"),
    ("hook", "pre-compact:auto", "other"),
    ("unknown", "gemini-session", "gemini"),
])
def test_a_memory_is_counted_under_who_wrote_it(writer, source, agent):
    assert attribute_memory(writer, source) == agent


def test_agent_stats_uses_the_writer_before_the_source(tmp_db):
    for writer, source in (("claude-code", "log-everything"), ("claude-code", ""),
                           ("codex-cli", "review-notes"), ("", "some-skill")):
        add_memory(MemoryEntry(content=f"note by {writer} {source}", type="note", project="acme",
                               writer=writer, source=source), db_path=tmp_db)
    counts = {a["agent"]: a["memories"] for a in agent_stats(db_path=tmp_db)["agents"]}
    assert counts == {"claude": 2, "codex": 1, "other": 1}


def test_a_project_with_a_confirmed_home_is_not_guessed_a_second_one(tmp_db):
    ws.apply_manifest({"machine": "m", "root_id": "git", "abs_path": r"C:\work", "full": True,
                       "files": [{"rel_path": "Other/App/notes.md", "sha256": "a"},
                                 {"rel_path": "Other/Game/plan.md", "sha256": "b"},
                                 {"rel_path": "Loose/readme.md", "sha256": "c"}],
                       "markers": []}, db_path=tmp_db)
    ws.bind_folder("git", "Other/App", "app", "tool", db_path=tmp_db)
    for slug in ("app", "loose"):
        upsert_project(Project(slug=slug, name=slug), db_path=tmp_db)
    entry = MemoryEntry(content="Wrote Other/App/notes.md", type="note", project="app")
    add_memory(entry, db_path=tmp_db)
    rs.link_memory_files(entry, tmp_db)
    assert ws.owner_project("git", "Other/Game/plan.md", db_path=tmp_db) is None
    # a project with no home at all still gets its first guess
    loose = MemoryEntry(content="Read Loose/readme.md", type="note", project="loose")
    add_memory(loose, db_path=tmp_db)
    rs.link_memory_files(loose, tmp_db)
    owner = ws.owner_project("git", "Loose/readme.md", db_path=tmp_db)
    assert (owner["project"], owner["how"]) == ("loose", "memory")


def test_migration_012_adds_the_columns_and_marks_verbatim_summaries(tmp_db):
    conn = connect(tmp_db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        assert {"summary_source", "verified_at", "verified_by", "corrected_by"} <= cols
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'entity_patterns'").fetchone()
        applied = [r[0] for r in conn.execute("SELECT filename FROM schema_migrations")]
        assert "012_v33.sql" in applied
    finally:
        conn.close()


async def test_the_core_list_keeps_room_under_its_budget(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_TOOLS", raising=False)
    payload = [t.model_dump(exclude_none=True, by_alias=True) for t in await T.list_tools()]
    size = len(json.dumps(payload, separators=(",", ":")))
    assert size < 8000, size
    add = next(t for t in payload if t["name"] == "add_memory")
    assert {"summary", "importance", "supersedes", "corrects", "closes", "refs"} <= set(
        add["inputSchema"]["properties"])
    admin = next(t for t in payload if t["name"] == "brain_admin")
    assert {"supersede", "close_loop", "verify_memory", "get_entity_context",
            "entity_patterns"} <= set(admin["inputSchema"]["properties"]["action"]["enum"])
