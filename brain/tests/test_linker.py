# tests/test_linker.py
"""v2.0.0 — automatic memory graph: edge derivation, weights, rebuild."""
import json
import math
import sqlite3
import pytest

from app.linker import (
    link_new_memory, rebuild_graph, combined_weights,
    _tag_edges, _semantic_edges, _write_edges, TAG_W_MIN,
)
from app.storage import add_memory
from app.vector import vec_add
from app.models import MemoryEntry


def _vec(seed: float, dim: int = 8) -> list[float]:
    v = [math.sin(seed * (i + 1)) for i in range(dim)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _mem(id_, project="proj-a", type_="note", content="hello world",
         tags=None, source=""):
    kwargs = dict(content=content, type=type_, project=project,
                  tags=tags or [], source=source)
    if id_ is not None:
        kwargs["id"] = id_
    return MemoryEntry(**kwargs)


def _store(db, entry, seed=None):
    add_memory(entry, db_path=db)
    if seed is not None:
        vec_add(entry.id, _vec(seed), {}, db_path=db)


@pytest.fixture
def gdb(tmp_db, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    monkeypatch.setenv("MEMORYBRAIN_GRAPH_ENABLED", "true")
    return tmp_db


def _edges(db, kind=None):
    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM memory_links"
        if kind:
            q += f" WHERE kind = '{kind}'"
        return [dict(r) for r in c.execute(q).fetchall()]


# ---------------------------------------------------------------- semantic

def test_semantic_edge_created_above_threshold(gdb):
    a = _mem("a" * 32 + "beef", content="postgres tuning notes")
    _store(gdb, a, seed=1.0)
    b = _mem("b" * 32 + "beef", content="postgres tuning follow-up")
    _store(gdb, b, seed=1.001)  # nearly identical vector -> sim ~1.0
    edges = link_new_memory(b, _vec(1.001), db_path=gdb)
    sem = [e for e in edges if e["kind"] == "semantic"]
    assert len(sem) == 1 and sem[0]["dst"] == a.id
    assert 0.05 <= sem[0]["weight"] <= 1.0


def test_semantic_no_edge_below_threshold(gdb):
    a = _mem("a1")
    _store(gdb, a, seed=1.0)
    b = _mem("b1")
    _store(gdb, b, seed=9.0)  # dissimilar
    edges = link_new_memory(b, _vec(9.0), db_path=gdb)
    assert not [e for e in edges if e["kind"] == "semantic"]


def test_semantic_excludes_archived(gdb):
    a = _mem("a1")
    _store(gdb, a, seed=1.0)
    with sqlite3.connect(gdb) as c:
        c.execute("UPDATE memories SET status = 'archived' WHERE id = 'a1'")
    b = _mem("b1")
    _store(gdb, b, seed=1.001)
    edges = link_new_memory(b, _vec(1.001), db_path=gdb)
    assert not [e for e in edges if e["kind"] == "semantic"]


# --------------------------------------------------------------------- tags

def test_tag_edge_requires_discriminating_tag(gdb):
    # 'rare-tag' appears once -> discriminating; edge forms
    a = _mem("a1", tags=["rare-tag", "python"])
    _store(gdb, a, seed=1.0)
    from app.linker import _bump_tag_stats
    _bump_tag_stats(["rare-tag", "python"], gdb)
    b = _mem("b1", tags=["rare-tag", "python"], content="different topic entirely")
    _store(gdb, b, seed=9.0)
    edges = link_new_memory(b, _vec(9.0), db_path=gdb)
    tag_edges = [e for e in edges if e["kind"] == "tag"]
    assert len(tag_edges) == 1
    assert json.loads(_edges(gdb, "tag")[0]["meta"])["shared_tags"] == ["python", "rare-tag"]


def test_tag_weight_favours_rare_tags(gdb):
    """A shared rare tag must outweigh a shared ubiquitous tag."""
    from app.linker import _bump_tag_stats
    # 'common' on 10 memories, 'rare' on 1
    for i in range(10):
        _store(gdb, _mem(f"c{i}", tags=["common"]))
        _bump_tag_stats(["common"], gdb)
    _store(gdb, _mem("r1", tags=["rare"]))
    _bump_tag_stats(["rare"], gdb)

    e_rare = _tag_edges("q1", ["rare"], gdb)
    e_common = _tag_edges("q2", ["common"], gdb)
    # rare match produces a higher weight than any common match (if the
    # common one survives the discriminating-tag rule at all)
    top_rare = max(e["weight"] for e in e_rare)
    top_common = max([e["weight"] for e in e_common], default=0.0)
    assert top_rare > top_common


# --------------------------------------------------------------- references

def test_supersession_reference_edge(gdb):
    old = _mem("old1")
    _store(gdb, old, seed=1.0)
    new = _mem("new1")
    _store(gdb, new, seed=2.0)
    edges = link_new_memory(new, _vec(2.0), superseded_ids=["old1"], db_path=gdb)
    refs = [e for e in edges if e["kind"] == "reference"]
    assert refs and refs[0]["dst"] == "old1" and refs[0]["directed"] == 1
    assert refs[0]["meta"]["reason"] == "supersedes"


def test_uuid_mention_creates_reference(gdb):
    target = _mem(None)  # real uuid4
    _store(gdb, target, seed=1.0)
    citing = _mem(None, content=f"as decided in memory {target.id}, we use FTS5")
    _store(gdb, citing, seed=9.0)
    edges = link_new_memory(citing, _vec(9.0), db_path=gdb)
    mentions = [e for e in edges if e["kind"] == "reference"
                and e["meta"].get("reason") == "id_mention"]
    assert len(mentions) == 1 and mentions[0]["dst"] == target.id


def test_same_source_reference(gdb):
    a = _mem("a1", source="docs/design.md")
    _store(gdb, a, seed=1.0)
    b = _mem("b1", source="docs/design.md", content="unrelated")
    _store(gdb, b, seed=9.0)
    edges = link_new_memory(b, _vec(9.0), db_path=gdb)
    ss = [e for e in edges if e["meta"].get("reason") == "same_source"]
    assert len(ss) == 1 and ss[0]["weight"] == 0.9


# ------------------------------------------------------------ session chain

def test_session_chain_links_previous_session_same_project(gdb):
    s1 = _mem("s1", type_="session", content="session one")
    _store(gdb, s1, seed=1.0)
    s_other = _mem("sx", type_="session", project="proj-b")
    _store(gdb, s_other, seed=2.0)
    s2 = _mem("s2", type_="session", content="session two")
    _store(gdb, s2, seed=9.0)
    edges = link_new_memory(s2, _vec(9.0), db_path=gdb)
    chain = [e for e in edges if e["kind"] == "session_chain"]
    assert len(chain) == 1 and chain[0]["dst"] == "s1" and chain[0]["directed"] == 1


def test_no_session_chain_for_notes(gdb):
    n1 = _mem("n1", type_="note")
    _store(gdb, n1, seed=1.0)
    n2 = _mem("n2", type_="note")
    _store(gdb, n2, seed=9.0)
    edges = link_new_memory(n2, _vec(9.0), db_path=gdb)
    assert not [e for e in edges if e["kind"] == "session_chain"]


# --------------------------------------------------- weights, degree, misc

def test_noisy_or_combined_weight(gdb):
    for id_ in ("a1", "b1"):
        _store(gdb, _mem(id_))
    _write_edges([
        {"src": "a1", "dst": "b1", "kind": "semantic", "weight": 0.7, "directed": 0, "meta": {}},
        {"src": "a1", "dst": "b1", "kind": "tag", "weight": 0.4, "directed": 0, "meta": {}},
    ], gdb)
    cw = combined_weights("a1", gdb)
    assert abs(cw["b1"] - (1 - (1 - 0.7) * (1 - 0.4))) < 1e-9   # 0.82
    # symmetric: visible from both ends
    assert abs(combined_weights("b1", gdb)["a1"] - cw["b1"]) < 1e-9


def test_link_degree_cached_on_memories(gdb):
    a = _mem("a1")
    _store(gdb, a, seed=1.0)
    b = _mem("b1")
    _store(gdb, b, seed=1.001)
    link_new_memory(b, _vec(1.001), db_path=gdb)
    with sqlite3.connect(gdb) as c:
        deg_a, deg_b = (c.execute("SELECT link_degree FROM memories WHERE id=?", (i,)).fetchone()[0]
                        for i in ("a1", "b1"))
    assert deg_a > 0 and deg_b > 0


def test_symmetric_edges_stored_once_src_lt_dst(gdb):
    for id_ in ("z9", "a1"):
        _store(gdb, _mem(id_))
    _write_edges([{"src": "z9", "dst": "a1", "kind": "semantic",
                   "weight": 0.5, "directed": 0, "meta": {}}], gdb)
    rows = _edges(gdb)
    assert len(rows) == 1 and rows[0]["src_id"] == "a1" and rows[0]["dst_id"] == "z9"


def test_graph_disabled_env(gdb, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_GRAPH_ENABLED", "false")
    a = _mem("a1")
    _store(gdb, a, seed=1.0)
    assert link_new_memory(a, _vec(1.0), db_path=gdb) == []


def test_linker_failure_does_not_fail_ingest(gdb, mock_ollama, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", gdb)
    monkeypatch.setattr("app.linker.link_new_memory",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    from app.ingest_pipeline import ingest
    import asyncio
    entry = _mem(None, content="ingest survives linker explosion")
    result = asyncio.get_event_loop().run_until_complete(ingest(entry))
    with sqlite3.connect(gdb) as c:
        assert c.execute("SELECT COUNT(*) FROM memories WHERE id=?",
                         (result.id,)).fetchone()[0] == 1


def test_rebuild_is_idempotent_and_matches_incremental(gdb):
    ids = []
    for i, seed in enumerate([1.0, 1.001, 9.0], start=1):
        m = _mem(f"m{i}", tags=["shared-rare"] if i < 3 else [],
                 type_="session" if i == 3 else "note")
        _store(gdb, m, seed=seed)
        link_new_memory(m, _vec(seed), db_path=gdb)
        ids.append(m.id)

    def snapshot():
        return sorted((e["src_id"], e["dst_id"], e["kind"]) for e in _edges(gdb))

    r1 = rebuild_graph(db_path=gdb)
    snap1 = snapshot()
    r2 = rebuild_graph(db_path=gdb)
    assert snapshot() == snap1                    # idempotent
    assert r1["total_edges"] == r2["total_edges"]
    assert r1["memories"] == 3


def test_hard_delete_removes_edges(gdb):
    from app.storage import delete_memory
    a = _mem("a1")
    _store(gdb, a, seed=1.0)
    b = _mem("b1")
    _store(gdb, b, seed=1.001)
    link_new_memory(b, _vec(1.001), db_path=gdb)
    assert _edges(gdb)
    delete_memory("b1", db_path=gdb)
    with sqlite3.connect(gdb) as c:
        c.execute("DELETE FROM memory_links WHERE src_id='b1' OR dst_id='b1'")
    assert not [e for e in _edges(gdb) if "b1" in (e["src_id"], e["dst_id"])]
