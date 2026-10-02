"""S3: a secret written through any door, in any field, never reaches the
database. Each path carries its own fake token; afterwards no table holds any
of them."""
import json

import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.mcp import tools as T

MODULES = ("brief", "conflicts", "consolidate", "exchange", "graph_queries", "ingest_pipeline",
           "linker", "main", "obsidian", "pins", "policy", "retrieval", "search", "timeline",
           "ui.editor", "ui.queries", "vector", "mcp.tools")


@pytest.fixture
def db(tmp_db, fake_provider, monkeypatch):
    for mod in MODULES:
        monkeypatch.setattr(f"app.{mod}.DB_PATH", tmp_db, raising=False)
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    return tmp_db


class Tokens:
    """A distinct fake GitHub token per write path (ghp_ + 36 characters)."""

    def __init__(self):
        self.by_path = {}

    def __call__(self, name: str) -> str:
        tok = f"ghp_FAKE{len(self.by_path):02d}" + "x" * 30
        assert len(tok) == 40
        self.by_path[name] = tok
        return tok


def _leaks(db_path, tokens: Tokens) -> dict:
    conn = connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'memories_fts_%'")]
        found = {}
        for table in tables:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]
            for col in cols:
                for name, tok in tokens.by_path.items():
                    n = conn.execute(f"SELECT COUNT(*) FROM '{table}' WHERE CAST(\"{col}\" AS TEXT) "
                                     "LIKE ?", (f"%{tok}%",)).fetchone()[0]
                    if n:
                        found.setdefault(name, []).append(f"{table}.{col}")
        return found
    finally:
        conn.close()


async def _call(name, args):
    return json.loads((await T.call_tool(name, args))[0].text)


@pytest.mark.asyncio
async def test_no_field_on_any_write_path_stores_a_secret(db):
    t = Tokens()
    rest = TestClient(__import__("app.main", fromlist=["app"]).app,
                      base_url="http://localhost:7741", headers={"X-Brain-Client": "test"})

    added = await _call("add_memory", {
        "content": "body " + t("mcp.content"), "type": "note", "project": "acme",
        "tags": ["tag-" + t("mcp.tags")], "source": "src " + t("mcp.source"),
        "refs": [{"path": "notes/" + t("mcp.refs") + ".md", "kind": "file"}]})
    assert "id" in added, added

    rest.post("/ingest/session", json={"content": "sess " + t("rest.session.content"),
                                       "project": "acme", "source": "s " + t("rest.session.source")})
    rest.post("/ingest/note", json={"content": "note " + t("rest.note.content"), "project": "acme",
                                    "tags": ["t-" + t("rest.note.tags")]})
    r = rest.post("/api/ui/edit/notes", json={"content": "ui " + t("ui.note.content"),
                                              "project": "acme", "tags": ["u-" + t("ui.note.tags")]})
    nid = r.json()["id"]
    rest.patch(f"/api/ui/edit/memories/{nid}", json={"tags": ["p-" + t("ui.patch.tags")]})
    rest.post("/api/ui/edit/projects", json={"slug": "proj2", "name": "N " + t("ui.project.name"),
                                             "one_liner": "o " + t("ui.project.one_liner")})
    rest.put("/api/ui/edit/policy", json={"project": "acme",
                                          "default_tags": ["d-" + t("ui.policy.default_tags")]})

    thread = await _call("post_task", {"project": "acme", "title": "ti", "body": "bo",
                                       "from_agent": "grok", "to_agent": "claude",
                                       "refs": ["r " + t("exchange.post.refs")]})
    await _call("reply_to_thread", {"thread_id": thread["thread_id"], "body": "re",
                                    "from_agent": "claude", "refs": ["rr " + t("exchange.reply.refs")]})
    await _call("set_project_info", {"project": "acme", "name": "nm " + t("project_info.name")})
    await _call("pin_memory", {"project": "acme", "memory_id": nid, "label": "lb " + t("pin.label")})
    await _call("brain_admin", {"action": "set_policy", "args": {
        "project": "acme", "default_tags": ["d-" + t("mcp.policy.default_tags")]}})
    await _call("brain_admin", {"action": "delete_memory", "args": {
        "memory_id": nid, "reason": "why " + t("delete.reason")}})
    rest.post("/workspace/scan", json={"machine": "m", "root_id": "r1", "abs_path": "/w",
                                       "full": True, "files": [{"rel_path": "a/README.md",
                                                                "title": "ti " + t("scan.title")}]})
    rest.post("/workspace/map", json={"root_id": "r1", "folders": [
        {"rel_path": "a", "project": "acme", "label": "l " + t("map.label")}]})
    rest.post("/workspace/bind", json={"project": "acme", "how": "init", "root_id": "r1",
                                       "rel_path": "g", "label": "bl " + t("bind.label")})

    assert _leaks(db, t) == {}
