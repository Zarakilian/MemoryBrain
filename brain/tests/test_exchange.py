# tests/test_exchange.py — Synapse / Agent Exchange (v2.4)
import json

import pytest

from app import exchange as ex
from app.models import MemoryEntry
from app.storage import add_memory


# ------------------------------------------------------------- normalization

def test_normalize_agent_aliases():
    assert ex.normalize_agent("Claude Code") == "claude"
    assert ex.normalize_agent("grok-cli") == "grok"
    assert ex.normalize_agent("ChatGPT") == "codex"
    assert ex.normalize_agent("OpenAI Codex") == "codex"
    assert ex.normalize_agent("gemini-2.5") == "gemini"
    assert ex.normalize_agent("") == ""
    assert ex.normalize_agent("My Custom Bot!") == "my-custom-bot"


def test_attribute_source_buckets_unknowns():
    # Free-form historical sources (file names, project strings, hook labels)
    # must not become pseudo-agents in the analytics.
    assert ex.attribute_source("Claude Code") == "claude"
    assert ex.attribute_source("grok") == "grok"
    assert ex.attribute_source("session hook") == "other"
    assert ex.attribute_source("src/app/main.py") == "other"
    assert ex.attribute_source("baby-bee-blossom") == "other"
    assert ex.attribute_source("") == "other"


# ------------------------------------------------------------------ threads

def test_post_task_and_get_thread(tmp_db):
    out = ex.post_task(
        project="my-app", title="Review: feature X",
        body="Implemented X in abc123", from_agent="grok",
        to_agent="codex", kind="review", refs=["mem-1"], db_path=tmp_db,
    )
    assert out["status"] == "open"
    assert out["assigned_to"] == "codex"

    t = ex.get_thread(out["thread_id"], db_path=tmp_db)
    assert t["title"] == "Review: feature X"
    assert t["kind"] == "review"
    assert t["created_by"] == "grok"
    assert len(t["messages"]) == 1
    assert t["messages"][0]["intent"] == "request"
    assert t["messages"][0]["refs"] == ["mem-1"]


def test_post_task_validation(tmp_db):
    with pytest.raises(ValueError):
        ex.post_task(project="p", title="", body="b", from_agent="grok",
                     db_path=tmp_db)
    with pytest.raises(ValueError):
        ex.post_task(project="p", title="t", body="b", from_agent="grok",
                     kind="nonsense", db_path=tmp_db)
    with pytest.raises(ValueError):
        ex.post_task(project="p", title="t", body="b", from_agent="",
                     db_path=tmp_db)


def test_reply_and_status_flow(tmp_db):
    out = ex.post_task(project="p", title="t", body="do it",
                       from_agent="grok", to_agent="codex", db_path=tmp_db)
    tid = out["thread_id"]

    r = ex.reply_to_thread(tid, body="2 issues found", from_agent="codex",
                           to_agent="grok", intent="review", status="review",
                           db_path=tmp_db)
    assert r["status"] == "review"

    t = ex.get_thread(tid, db_path=tmp_db)
    assert t["status"] == "review"
    assert len(t["messages"]) == 2

    s = ex.update_task_status(tid, "done", agent="grok", db_path=tmp_db)
    assert s["status"] == "done"
    assert ex.get_thread(tid, db_path=tmp_db)["status"] == "done"


def test_reply_missing_thread(tmp_db):
    r = ex.reply_to_thread("nope", body="x", from_agent="grok", db_path=tmp_db)
    assert "error" in r
    s = ex.update_task_status("nope", "done", db_path=tmp_db)
    assert "error" in s


def test_list_threads_filters(tmp_db):
    ex.post_task(project="a", title="t1", body="b", from_agent="grok",
                 to_agent="codex", db_path=tmp_db)
    out2 = ex.post_task(project="b", title="t2", body="b", from_agent="claude",
                        db_path=tmp_db)
    ex.update_task_status(out2["thread_id"], "done", db_path=tmp_db)

    assert ex.list_threads(project="a", db_path=tmp_db)["total"] == 1
    assert ex.list_threads(status="done", db_path=tmp_db)["total"] == 1
    assert ex.list_threads(status="any_open", db_path=tmp_db)["total"] == 1
    assert ex.list_threads(agent="codex", db_path=tmp_db)["total"] == 1
    assert ex.list_threads(db_path=tmp_db)["total"] == 2


# -------------------------------------------------------------------- inbox

def test_inbox_delivery_and_read_cursor(tmp_db):
    ex.post_task(project="p", title="Review me", body="please",
                 from_agent="grok", to_agent="codex", db_path=tmp_db)

    # Sender's own inbox stays empty (own messages don't count as unread).
    assert ex.get_inbox("grok", db_path=tmp_db)["total"] == 0

    # Recipient sees it once…
    inbox = ex.get_inbox("codex", db_path=tmp_db)
    assert inbox["total"] == 1
    assert inbox["threads"][0]["unread_count"] == 1
    # …and not again after the cursor advanced.
    assert ex.get_inbox("codex", db_path=tmp_db)["total"] == 0

    # A reply re-surfaces the thread for the other side.
    tid = inbox["threads"][0]["id"]
    ex.reply_to_thread(tid, body="done", from_agent="codex", to_agent="grok",
                       db_path=tmp_db)
    grok_inbox = ex.get_inbox("grok", db_path=tmp_db)
    assert grok_inbox["total"] == 1
    assert grok_inbox["threads"][0]["unread_messages"][0]["body"] == "done"


def test_inbox_broadcast_and_mark_read_false(tmp_db):
    ex.post_task(project="p", title="anyone?", body="opinions welcome",
                 from_agent="grok", to_agent="", db_path=tmp_db)
    # Broadcast reaches a third agent.
    inbox = ex.get_inbox("claude", mark_read=False, db_path=tmp_db)
    assert inbox["total"] == 1
    # mark_read=False leaves the cursor alone.
    assert ex.get_inbox("claude", db_path=tmp_db)["total"] == 1
    assert ex.get_inbox("claude", db_path=tmp_db)["total"] == 0

    # include_broadcast=False hides it.
    ex.post_task(project="p", title="again", body="more", from_agent="grok",
                 to_agent="", db_path=tmp_db)
    assert ex.get_inbox("gemini", include_broadcast=False,
                        db_path=tmp_db)["total"] == 0


def test_inbox_closed_threads_hidden(tmp_db):
    out = ex.post_task(project="p", title="t", body="b", from_agent="grok",
                       to_agent="codex", db_path=tmp_db)
    ex.update_task_status(out["thread_id"], "closed", db_path=tmp_db)
    assert ex.get_inbox("codex", db_path=tmp_db)["total"] == 0


# ---------------------------------------------------------------- analytics

def test_agent_stats_counts_messages_and_memories(tmp_db):
    add_memory(MemoryEntry(content="fact from grok", type="fact",
                           project="p", source="grok-cli"), db_path=tmp_db)
    add_memory(MemoryEntry(content="note from claude", type="note",
                           project="p", source="Claude Code"), db_path=tmp_db)
    add_memory(MemoryEntry(content="free-form source", type="note",
                           project="p", source="some/file/path.py"), db_path=tmp_db)
    out = ex.post_task(project="p", title="t", body="b", from_agent="grok",
                       to_agent="codex", db_path=tmp_db)
    ex.reply_to_thread(out["thread_id"], body="ok", from_agent="codex",
                       to_agent="grok", db_path=tmp_db)

    stats = ex.agent_stats(db_path=tmp_db)
    by_agent = {a["agent"]: a for a in stats["agents"]}
    assert by_agent["grok"]["memories"] == 1
    assert by_agent["grok"]["messages"] == 1
    assert by_agent["grok"]["threads_opened"] == 1
    assert by_agent["claude"]["memories"] == 1
    assert by_agent["codex"]["messages"] == 1
    assert by_agent["other"]["memories"] == 1      # bucketed, not a pseudo-agent
    assert stats["agents"][-1]["agent"] == "other"  # 'other' sorts last

    projects = {p["project"]: p for p in stats["projects"]}
    assert "p" in projects

    # project filter
    assert ex.agent_stats(project="nope", db_path=tmp_db)["agents"] == []


def test_agent_network_edges(tmp_db):
    out = ex.post_task(project="p", title="t", body="b", from_agent="grok",
                       to_agent="codex", db_path=tmp_db)
    tid = out["thread_id"]
    ex.reply_to_thread(tid, body="r1", from_agent="codex", to_agent="grok",
                       db_path=tmp_db)
    # Broadcast reply answers the previous speaker (codex).
    ex.reply_to_thread(tid, body="r2", from_agent="claude", to_agent="",
                       db_path=tmp_db)

    net = ex.agent_network(db_path=tmp_db)
    agents = {n["agent"] for n in net["nodes"]}
    assert {"grok", "codex", "claude"} <= agents
    edges = {(e["source"], e["target"]): e["count"] for e in net["edges"]}
    assert edges[("grok", "codex")] == 1
    assert edges[("codex", "grok")] == 1
    assert edges[("claude", "codex")] == 1


# -------------------------------------------------------------- MCP surface

@pytest.mark.asyncio
async def test_mcp_tools_roundtrip(tmp_db, monkeypatch):
    import app.mcp.tools as t
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)

    created = json.loads(await t.handle_post_task(
        project="p", title="t", body="b", from_agent="grok", to_agent="codex",
        kind="review",
    ))
    assert "thread_id" in created

    inbox = json.loads(await t.handle_get_agent_inbox(agent="codex"))
    assert inbox["total"] == 1

    replied = json.loads(await t.handle_reply_to_thread(
        thread_id=created["thread_id"], body="ok", from_agent="codex",
        status="done",
    ))
    assert replied["status"] == "done"

    thread = json.loads(await t.handle_get_thread(created["thread_id"]))
    assert len(thread["messages"]) == 2

    listed = json.loads(await t.handle_list_threads(status="done"))
    assert listed["total"] == 1

    stats = json.loads(await t.handle_get_agent_stats())
    assert "network" in stats

    bad = json.loads(await t.handle_post_task(
        project="p", title="", body="b", from_agent="grok"))
    assert "error" in bad


def test_tool_registry_declares_new_tools():
    from app.mcp.tools import TOOL_NAMES, _TOOL_ARGS
    for name in ("post_task", "get_agent_inbox", "reply_to_thread",
                 "update_task_status", "list_threads", "get_thread",
                 "get_agent_stats"):
        assert name in TOOL_NAMES
        assert name in _TOOL_ARGS
