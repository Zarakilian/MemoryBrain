"""v3 retrieval eval harness: metrics, label runs, REST search, synthetic baseline."""
import hashlib
import json
import math
import re

import pytest

from app.evaluation import (recall_at_k, reciprocal_rank, run_labels, score_run,
                            synthetic_fixture)


# ------------------------------------------------------------- metrics

def test_recall_at_k():
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, 1) == 0.5
    assert recall_at_k(["a", "b", "c"], {"a", "c"}, 3) == 1.0
    assert recall_at_k(["x", "y"], {"a"}, 5) == 0.0
    assert recall_at_k(["a"], set(), 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(["a", "b"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y", "a"], {"a", "b"}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], {"a"}) == 0.0


def test_score_run_averages_and_lists_each_query():
    report = score_run({"q1": ["a", "x"], "q2": ["y", "b"]}, {"q1": {"a"}, "q2": {"b"}}, ks=(1, 5))
    assert report["queries"] == 2
    assert report["recall@1"] == 0.5 and report["recall@5"] == 1.0
    assert report["mrr"] == pytest.approx(0.75)
    assert [row["query"] for row in report["per_query"]] == ["q1", "q2"]
    assert report["per_query"][1]["rr"] == 0.5


@pytest.mark.asyncio
async def test_run_labels_with_a_stub_search(tmp_path):
    labels = tmp_path / "labels.jsonl"
    labels.write_text("\n".join(json.dumps(x) for x in [
        {"query": "where is the invoice export", "project": "acme", "relevant": ["m1"]},
        {"query": "which host runs the db", "project": None, "relevant": ["m2", "m3"]},
    ]) + "\n", encoding="utf-8")
    calls = []

    async def search(query, project):
        calls.append((query, project))
        return [{"id": "m1"}, {"id": "m3"}]
    report = await run_labels(labels, search)
    assert calls == [("where is the invoice export", "acme"), ("which host runs the db", None)]
    assert report["queries"] == 2
    assert report["recall@5"] == pytest.approx(0.75) and report["mrr"] == pytest.approx(0.75)


# ------------------------------------------------------------- REST search

def test_rest_search_returns_the_mcp_list_and_changes_nothing(tmp_db, fake_provider, monkeypatch):
    """GET /search is read-only: no retrieval row, no recall boost. A cross-site
    <img src=".../search?q=..."> must not be able to steer ranking feedback."""
    from fastapi.testclient import TestClient
    from app.db import connect
    from app.main import app
    from app.models import MemoryEntry
    from app.storage import add_memory

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    for mod in ("app.main", "app.search", "app.mcp.tools"):
        monkeypatch.setattr(f"{mod}.DB_PATH", tmp_db)
    entry = MemoryEntry(content="the quarterly invoice export", type="note", project="acme",
                        importance=3)
    add_memory(entry, db_path=tmp_db)
    r = TestClient(app).get("/search", params={"q": "invoice", "project": "acme", "limit": 5})
    assert r.status_code == 200
    assert [x["id"] for x in r.json()] == [entry.id]
    assert float(r.headers["X-Took-Ms"]) >= 0
    conn = connect(tmp_db)
    try:
        events = conn.execute("SELECT COUNT(*) FROM retrieval_events").fetchone()[0]
        recalled = conn.execute("SELECT last_recalled, strength FROM memories WHERE id = ?",
                                (entry.id,)).fetchone()
    finally:
        conn.close()
    assert events == 0
    assert recalled["last_recalled"] is None and recalled["strength"] == pytest.approx(1.0)


def test_retrieval_log_groups_queries_for_labelling(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.retrieval import record_retrieval

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.main.DB_PATH", tmp_db)
    record_retrieval("invoice export", ["m1", "m2"], project="acme", db_path=tmp_db)
    record_retrieval("invoice export", ["m1"], chosen_id="m1", project="acme", db_path=tmp_db)
    record_retrieval("db host", ["m3"], project=None, db_path=tmp_db)
    rows = TestClient(app).get("/admin/retrieval-log").json()["queries"]
    by_query = {(r["query"], r["project"]): r for r in rows}
    assert by_query[("invoice export", "acme")]["relevant"] == ["m1"]
    assert by_query[("db host", None)]["relevant"] == []


# ------------------------------------------------------------- synthetic baseline

class HashedBagOfWords:
    """Deterministic 128-dimension embedder: hashed word counts, L2-normalised."""
    name = "hashbow"

    def __init__(self):
        self._embed_model = "hashbow-128"
        self._summarise_model = "none"

    async def embed(self, text):
        vector = [0.0] * 128
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 128] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    async def embed_many(self, texts):
        return [await self.embed(t) for t in texts]

    async def summarise(self, content, max_sentences=3):
        return content[:200]

    async def score_importance(self, content):
        return 3


def test_the_fixture_is_neutral_and_well_formed():
    memories, labels = synthetic_fixture()
    assert len(memories) == 30 and len(labels) == 20
    ids = {m["id"] for m in memories}
    assert len({m["project"] for m in memories}) == 3
    assert all(set(label["relevant"]) <= ids for label in labels)
    long_session = max(memories, key=lambda m: len(m["content"]))
    assert len(long_session["content"]) > 2000
    assert any(set(label["relevant"]) == {long_session["id"]} for label in labels)


@pytest.mark.asyncio
async def test_synthetic_baseline_runs_and_reports(tmp_db, monkeypatch, capsys):
    import app.summarise as s
    from app.ingest_pipeline import ingest
    from app.models import MemoryEntry
    from app.search import hybrid_search

    monkeypatch.setattr(s, "_provider", HashedBagOfWords())
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    memories, labels = synthetic_fixture()
    for m in memories:
        await ingest(MemoryEntry(id=m["id"], content=m["content"], type=m["type"],
                                 project=m["project"], importance=3))
    results, wanted = {}, {}
    for label in labels:
        hits = await hybrid_search(label["query"], limit=10, project=label["project"],
                                   db_path=tmp_db)
        results[label["query"]] = [h["id"] for h in hits]
        wanted[label["query"]] = set(label["relevant"])
    report = score_run(results, wanted)
    assert report["queries"] == 20
    for key in ("recall@5", "recall@10", "mrr"):
        assert 0.0 <= report[key] <= 1.0
    worst = sorted(report["per_query"], key=lambda r: (r["rr"], r["recall@10"]))[:3]
    print(f"\nSYNTHETIC BASELINE recall@5={report['recall@5']} recall@10={report['recall@10']} "
          f"mrr={report['mrr']}")
    for row in worst:
        print(f"  worst: rr={row['rr']:.2f} r@10={row['recall@10']:.2f} {row['query']}")


def test_unlabelled_questions_are_skipped_not_scored_as_misses():
    report = score_run({"q1": ["a"], "q2": ["b"]}, {"q1": {"a"}, "q2": set()})
    assert report["queries"] == 1 and report["skipped"] == 1
    assert report["mrr"] == 1.0 and report["recall@5"] == 1.0
    assert [row["query"] for row in report["per_query"]] == ["q1"]
