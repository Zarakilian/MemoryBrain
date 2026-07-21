# tests/test_consolidate.py
"""The consolidation cycle: beliefs distilled from clusters (with
derived_from provenance), source damping, belief-scoped supersession,
reinforcement + decay, contradiction flagging, open-loop extraction,
the /admin/consolidate endpoint and the MCP tool."""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.models import MemoryEntry, Project
from app.storage import (add_memory, upsert_project, record_recall,
                         decay_strengths, scale_strengths, get_strengths,
                         STRENGTH_FLOOR)
from app.linker import _write_edges
from app.consolidate import consolidate, LOOP_TAG
from app.search import strength_factor, reciprocal_rank_fusion


def _mem(id_, proj="proj-a", type_="session", content=None, ts=None,
         summary=None):
    e = MemoryEntry(
        id=id_, type=type_, project=proj,
        content=content or (f"the {id_} work on the audit connector " * 12),
        summary=summary if summary is not None else f"summary {id_}",
    )
    if ts:
        e.timestamp = ts
    return e


def _seed_cluster(db, ids, proj="proj-a"):
    upsert_project(Project(slug=proj, name=proj), db_path=db)
    for i in ids:
        add_memory(_mem(i, proj=proj), db_path=db)
    _write_edges([
        {"src": a, "dst": b, "kind": "session_chain", "weight": 1.0,
         "directed": 1, "meta": {}}
        for a, b in zip(ids, ids[1:])
    ], db)


@pytest.fixture
def cdb(tmp_db):
    """tmp_db with every DB_PATH the cycle touches pointed at it."""
    patches = [
        patch("app.ingest_pipeline.DB_PATH", tmp_db),
        patch("app.consolidate.DB_PATH", tmp_db),
        patch("app.search.DB_PATH", tmp_db),
    ]
    for p in patches:
        p.start()
    yield tmp_db
    for p in patches:
        p.stop()


NO_SUPERSESSION = AsyncMock(return_value=([], []))


# ------------------------------------------------------------ the beliefs

@pytest.mark.asyncio
async def test_consolidate_distils_cluster_into_belief(cdb, mock_ollama):
    _seed_cluster(cdb, ["a1", "a2", "a3", "a4"])
    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        report = await consolidate(project="proj-a", db_path=cdb)

    assert len(report["projects"]) == 1
    pr = report["projects"][0]
    assert len(pr["beliefs"]) == 1
    assert pr["beliefs"][0]["sources"] == 4

    from app.storage import get_memory, _connect
    belief_id = pr["beliefs"][0]["id"]
    belief = get_memory(belief_id, db_path=cdb)
    assert belief.type == "belief"
    assert belief.status == "active"
    assert "belief" in belief.tags
    assert belief.source == "consolidation"

    with _connect(cdb) as conn:
        derived = conn.execute(
            """SELECT dst_id FROM memory_links
               WHERE src_id = ? AND kind = 'derived_from'""",
            (belief_id,)).fetchall()
    assert {r["dst_id"] for r in derived} == {"a1", "a2", "a3", "a4"}

    # 2. the sources sank a little — the belief speaks first for them now
    strengths = get_strengths(["a1", "a2", "a3", "a4"], db_path=cdb)
    assert all(abs(s - 0.8) < 1e-6 for s in strengths.values())


@pytest.mark.asyncio
async def test_consolidate_is_idempotent_per_cluster(cdb, mock_ollama):
    _seed_cluster(cdb, ["b1", "b2", "b3"])
    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        first = await consolidate(project="proj-a", db_path=cdb)
        second = await consolidate(project="proj-a", db_path=cdb)

    assert len(first["projects"][0]["beliefs"]) == 1
    assert second["projects"][0]["beliefs"] == []
    assert second["projects"][0]["skipped_clusters"] >= 1

    from app.storage import _connect
    with _connect(cdb) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE type = 'belief'").fetchone()[0]
    assert n == 1


@pytest.mark.asyncio
async def test_small_scatter_produces_no_belief(cdb, mock_ollama):
    upsert_project(Project(slug="proj-a", name="A"), db_path=cdb)
    add_memory(_mem("solo1"), db_path=cdb)   # no edges, below MIN_CLUSTER_SIZE
    add_memory(_mem("solo2"), db_path=cdb)
    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        report = await consolidate(project="proj-a", db_path=cdb)
    assert report["projects"][0]["beliefs"] == []


@pytest.mark.asyncio
async def test_belief_supersedes_only_prior_beliefs(cdb, mock_ollama):
    """The scoping guard: a belief embeds close to its raw sources, so it
    must never auto-supersede them — only prior beliefs."""
    from app.ingest_pipeline import _check_supersession
    from app.vector import vec_add

    upsert_project(Project(slug="proj-a", name="A"), db_path=cdb)
    add_memory(_mem("raw1", type_="note"), db_path=cdb)
    add_memory(_mem("oldbelief", type_="belief"), db_path=cdb)
    emb = [0.1] * 768
    vec_add("raw1", emb, {"project": "proj-a", "type": "note",
                          "status": "active"}, db_path=cdb)
    vec_add("oldbelief", emb, {"project": "proj-a", "type": "belief",
                               "status": "active"}, db_path=cdb)

    entry = _mem("newbelief", type_="belief")
    superseded, _potential = await _check_supersession(entry, emb)
    assert "oldbelief" in superseded          # identical prior belief: replaced
    assert "raw1" not in superseded           # raw source: never


# --------------------------------------------- reinforcement + decay

def test_record_recall_strengthens_and_stamps(tmp_db):
    add_memory(_mem("r1"), db_path=tmp_db)
    assert get_strengths(["r1"], db_path=tmp_db)["r1"] == 1.0
    assert record_recall(["r1"], db_path=tmp_db) == 1
    s = get_strengths(["r1"], db_path=tmp_db)["r1"]
    assert s == pytest.approx(1.25)
    from app.storage import _connect
    with _connect(tmp_db) as conn:
        lr = conn.execute("SELECT last_recalled FROM memories WHERE id='r1'"
                          ).fetchone()[0]
    assert lr is not None
    assert record_recall([], db_path=tmp_db) == 0
    assert record_recall(["missing"], db_path=tmp_db) == 0


def test_decay_touches_only_the_unrecalled_and_floors(tmp_db):
    old_ts = datetime.now(timezone.utc) - timedelta(days=60)
    add_memory(_mem("old1", ts=old_ts), db_path=tmp_db)
    add_memory(_mem("fresh1"), db_path=tmp_db)              # recent timestamp
    add_memory(_mem("recalled1", ts=old_ts), db_path=tmp_db)
    record_recall(["recalled1"], db_path=tmp_db)            # touched today

    n = decay_strengths(idle_days=14, db_path=tmp_db)
    assert n == 1                                           # only old1
    s = get_strengths(["old1", "fresh1", "recalled1"], db_path=tmp_db)
    assert s["old1"] == pytest.approx(0.9)
    assert s["fresh1"] == 1.0
    assert s["recalled1"] == pytest.approx(1.25)

    for _ in range(40):                                     # grind to the floor
        decay_strengths(idle_days=14, db_path=tmp_db)
    assert get_strengths(["old1"], db_path=tmp_db)["old1"] == pytest.approx(
        STRENGTH_FLOOR)


def test_strength_factor_bounds_and_ranking_effect():
    assert strength_factor(1.0) == pytest.approx(1.0)
    assert strength_factor(0.2) < 1.0
    assert strength_factor(3.0) > 1.0
    assert strength_factor(99.0) == strength_factor(3.0)    # clamped
    assert strength_factor(1.0, weight=0.0) == 1.0          # disabled

    kw = [{"id": "weak", "timestamp": "2026-07-01T00:00:00+00:00"},
          {"id": "strong", "timestamp": "2026-07-01T00:00:00+00:00"}]
    # equal RRF rank contributions except order; strength flips the outcome
    order = reciprocal_rank_fusion(kw, [], decay_rate=0,
                                   strengths={"weak": 0.3, "strong": 2.5})
    assert order[0] == "strong"


# ------------------------------------------------------------ contradictions

def _unit(vec):
    n = math.sqrt(sum(x * x for x in vec))
    return [x / n for x in vec]


@pytest.mark.asyncio
async def test_conflicts_flagged_in_warn_zone_only(cdb, mock_ollama):
    from app.vector import vec_add
    upsert_project(Project(slug="proj-a", name="A"), db_path=cdb)
    for mid in ("f1", "f2", "f3"):
        add_memory(_mem(mid, type_="fact"), db_path=cdb)

    # f1↔f2 at cos ≈ 0.85 (warn zone) — f3 orthogonal to both
    v1 = _unit([1, 0, 0, 0, 0, 0, 0, 0])
    v2 = _unit([0.85, math.sqrt(1 - 0.85 ** 2), 0, 0, 0, 0, 0, 0])
    v3 = _unit([0, 0, 1, 0, 0, 0, 0, 0])
    meta = {"project": "proj-a", "type": "fact", "status": "active"}
    vec_add("f1", v1, meta, db_path=cdb)
    vec_add("f2", v2, meta, db_path=cdb)
    vec_add("f3", v3, meta, db_path=cdb)

    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        report = await consolidate(project="proj-a", db_path=cdb)
    assert report["projects"][0]["conflicts"] == 1

    from app.storage import _connect
    with _connect(cdb) as conn:
        rows = conn.execute(
            "SELECT src_id, dst_id FROM memory_links WHERE kind='conflicts_with'"
        ).fetchall()
    assert len(rows) == 1
    assert {rows[0]["src_id"], rows[0]["dst_id"]} == {"f1", "f2"}

    # idempotent: a second run does not duplicate the flag
    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        second = await consolidate(project="proj-a", db_path=cdb)
    assert second["projects"][0]["conflicts"] == 0


# ---------------------------------------------------------------- open loops

@pytest.mark.asyncio
async def test_open_loops_extracted_once(cdb, mock_ollama):
    upsert_project(Project(slug="proj-a", name="A"), db_path=cdb)
    add_memory(_mem("s1", content=(
        "We shipped the exporter today. " * 20
        + "\nTODO: patch the staging box before Friday\n"
        + "Also we still need to rotate the API keys for the audit runner.\n"
    )), db_path=cdb)

    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        report = await consolidate(project="proj-a", db_path=cdb)
    assert report["projects"][0]["loops"] == 2

    from app.storage import _connect
    with _connect(cdb) as conn:
        rows = conn.execute(
            "SELECT content, tags FROM memories WHERE tags LIKE ?",
            (f'%{LOOP_TAG}%',)).fetchall()
    assert len(rows) == 2
    assert any("patch the staging box" in r["content"] for r in rows)

    # deduplicated by content hash on the second run
    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        second = await consolidate(project="proj-a", db_path=cdb)
    assert second["projects"][0]["loops"] == 0


# ------------------------------------------------- endpoint + MCP + UI

@pytest.fixture
def api_client(cdb, mock_ollama, monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_VECTOR_BACKEND", "sqlite_vec")
    patches = [
        patch("app.ui.queries.DB_PATH", cdb),
        patch("app.ui.editor.DB_PATH", cdb),
        patch("app.graph_queries.DB_PATH", cdb),
        patch("app.main.DB_PATH", cdb),
    ]
    for p in patches:
        p.start()
    from app.main import app
    yield TestClient(app)
    for p in patches:
        p.stop()


def test_admin_consolidate_endpoint(api_client):
    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        r = api_client.post("/admin/consolidate")
    assert r.status_code == 200
    body = r.json()
    assert "projects" in body and "decayed" in body


def test_admin_consolidate_requires_key_when_set(api_client, monkeypatch):
    monkeypatch.setattr("app.auth._API_KEY", "sekrit")
    assert api_client.post("/admin/consolidate").status_code == 401


def test_ui_conflicts_endpoint_shape(api_client, cdb):
    upsert_project(Project(slug="proj-a", name="A"), db_path=cdb)
    add_memory(_mem("c1", type_="fact"), db_path=cdb)
    add_memory(_mem("c2", type_="fact"), db_path=cdb)
    _write_edges([{"src": "c1", "dst": "c2", "kind": "conflicts_with",
                   "weight": 0.85, "directed": 0,
                   "meta": {"cos_sim": 0.85}}], cdb)
    body = api_client.get("/api/ui/conflicts").json()
    assert body["total"] == 1
    pair = body["pairs"][0]
    assert {pair["a"]["id"], pair["b"]["id"]} == {"c1", "c2"}
    assert pair["similarity"] == pytest.approx(0.85)
    # archiving one side silences the pair
    from app.storage import archive_memory
    archive_memory("c2", superseded_by="c1", db_path=cdb)
    assert api_client.get("/api/ui/conflicts").json()["total"] == 0


def test_ui_recall_endpoint(api_client, cdb):
    add_memory(_mem("u1"), db_path=cdb)
    r = api_client.post("/api/ui/edit/memories/u1/recall")
    assert r.status_code == 200 and r.json()["recalled"] == "u1"
    assert get_strengths(["u1"], db_path=cdb)["u1"] == pytest.approx(1.25)
    assert api_client.post("/api/ui/edit/memories/nope/recall").status_code == 404


@pytest.mark.asyncio
async def test_mcp_consolidate_tool_listed_and_callable(cdb, mock_ollama):
    from app.mcp.tools import list_tools, call_tool
    names = {t.name for t in await list_tools()}
    assert "consolidate_memory" in names

    with patch("app.ingest_pipeline._check_supersession", NO_SUPERSESSION):
        out = await call_tool("consolidate_memory", {})
    body = json.loads(out[0].text)
    assert "projects" in body and "decayed" in body


@pytest.mark.asyncio
async def test_mcp_retrieval_reinforces(cdb, mock_ollama):
    from app.mcp.tools import handle_get_memory
    add_memory(_mem("m1"), db_path=cdb)
    with patch("app.mcp.tools.DB_PATH", cdb):
        await handle_get_memory("m1")
    assert get_strengths(["m1"], db_path=cdb)["m1"] == pytest.approx(1.25)
