# tests/test_graph_tools.py
"""v2.0.0 — get_related_memories / get_memory_graph MCP tools + graph queries."""
import json
import math
import pytest

from app.graph_queries import get_related, get_graph
from app.linker import _write_edges
from app.storage import add_memory
from app.models import MemoryEntry
from app.mcp.tools import (
    handle_get_related_memories, handle_get_memory_graph, list_tools,
)


def _mem(id_, project="proj-a", type_="note", importance=3):
    return MemoryEntry(id=id_, content=f"content {id_}", summary=f"summary {id_}",
                       type=type_, project=project, importance=importance)


@pytest.fixture
def gdb(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    for id_, proj in [("a1", "proj-a"), ("b1", "proj-a"), ("c1", "proj-b")]:
        add_memory(_mem(id_, project=proj), db_path=tmp_db)
    _write_edges([
        {"src": "a1", "dst": "b1", "kind": "semantic", "weight": 0.7, "directed": 0, "meta": {"cos_sim": 0.74}},
        {"src": "a1", "dst": "b1", "kind": "tag", "weight": 0.4, "directed": 0, "meta": {"shared_tags": ["x"]}},
        {"src": "c1", "dst": "a1", "kind": "reference", "weight": 1.0, "directed": 1, "meta": {"reason": "supersedes"}},
    ], tmp_db)
    return tmp_db


# ------------------------------------------------------------- get_related

def test_related_ranked_by_combined_weight(gdb):
    r = get_related("a1", min_weight=0.1, db_path=gdb)
    ids = [x["id"] for x in r["related"]]
    assert ids[0] == "c1"                       # reference w=1.0 beats 0.82
    b = next(x for x in r["related"] if x["id"] == "b1")
    assert abs(b["w_combined"] - 0.82) < 1e-4   # noisy-OR(0.7, 0.4)
    assert set(b["kinds"]) == {"semantic", "tag"}
    assert any(e["meta"].get("cos_sim") for e in b["explanations"])


def test_related_min_weight_filters(gdb):
    r = get_related("a1", min_weight=0.9, db_path=gdb)
    assert [x["id"] for x in r["related"]] == ["c1"]


def test_related_kinds_filter(gdb):
    r = get_related("a1", min_weight=0.1, kinds=["tag"], db_path=gdb)
    assert [x["id"] for x in r["related"]] == ["b1"]
    assert r["related"][0]["kinds"] == ["tag"]


def test_related_unknown_memory_returns_none(gdb):
    assert get_related("nope", db_path=gdb) is None


def test_related_excludes_archived_by_default(gdb):
    import sqlite3
    with sqlite3.connect(gdb) as c:
        c.execute("UPDATE memories SET status = 'archived' WHERE id = 'b1'")
    r = get_related("a1", min_weight=0.1, db_path=gdb)
    assert "b1" not in [x["id"] for x in r["related"]]
    r2 = get_related("a1", min_weight=0.1, include_archived=True, db_path=gdb)
    assert "b1" in [x["id"] for x in r2["related"]]


def test_directed_reference_appears_as_backlink(gdb):
    """c1 -> a1 (supersedes). From c1: outgoing. From a1: incoming backlink."""
    out = get_related("c1", min_weight=0.1, db_path=gdb)["related"]
    a1 = next(x for x in out if x["id"] == "a1")
    assert a1["explanations"][0]["direction"] == "out"
    incoming = get_related("a1", min_weight=0.1, db_path=gdb)["related"]
    c1 = next(x for x in incoming if x["id"] == "c1")
    assert c1["explanations"][0]["direction"] == "in"
    assert c1["w_combined"] >= 0.99  # capped at 0.999 by the noisy-OR guard


# --------------------------------------------------------------- get_graph

def test_graph_nodes_edges_shape(gdb):
    g = get_graph(db_path=gdb, min_weight=0.1)
    assert {n["id"] for n in g["nodes"]} == {"a1", "b1", "c1"}
    assert g["truncated"] is False
    pair = next(e for e in g["edges"] if {e["src"], e["dst"]} == {"a1", "b1"})
    assert abs(pair["w"] - 0.82) < 1e-4
    assert set(pair["kinds"]) == {"semantic", "tag"}
    node = g["nodes"][0]
    assert {"id", "label", "type", "project", "importance", "timestamp", "degree"} <= set(node)


def test_graph_project_filter_drops_cross_project_edges(gdb):
    g = get_graph(project="proj-a", db_path=gdb, min_weight=0.1)
    assert {n["id"] for n in g["nodes"]} == {"a1", "b1"}
    assert all({e["src"], e["dst"]} <= {"a1", "b1"} for e in g["edges"])


def test_graph_max_nodes_truncates(gdb):
    g = get_graph(db_path=gdb, max_nodes=2, min_weight=0.1)
    assert len(g["nodes"]) == 2 and g["truncated"] is True
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in g["edges"])


def test_graph_empty_db(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    g = get_graph(db_path=tmp_db)
    assert g == {"nodes": [], "edges": [], "truncated": False}


# ---------------------------------------------------------------- MCP layer

@pytest.mark.asyncio
async def test_mcp_related_tool_shape(gdb, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", gdb)
    out = json.loads(await handle_get_related_memories("a1", min_weight=0.1))
    assert out["memory_id"] == "a1" and out["related"]


@pytest.mark.asyncio
async def test_mcp_related_tool_not_found(gdb, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", gdb)
    out = json.loads(await handle_get_related_memories("missing"))
    assert "error" in out


@pytest.mark.asyncio
async def test_mcp_graph_tool_shape(gdb, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", gdb)
    out = json.loads(await handle_get_memory_graph(min_weight=0.1))
    assert "nodes" in out and "edges" in out and "truncated" in out


@pytest.mark.asyncio
async def test_seventeen_tools_registered():
    from app.mcp.tools import TOOL_NAMES
    tools = await list_tools()
    names = {t.name for t in tools}
    assert names == set(TOOL_NAMES)
    assert len(names) == 17
    # v2.2 context-bank tools
    assert {
        "get_project_brief", "list_conflicts", "resolve_conflict",
        "dismiss_conflict", "pin_memory", "unpin_memory", "list_pins",
    } <= names
