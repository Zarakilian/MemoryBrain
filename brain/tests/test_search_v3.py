"""v3 search: questions find things, long tails are searchable, age stops
outranking truth, no walls of one session, point-in-time queries."""
from datetime import datetime, timedelta, timezone

import pytest

import app.summarise as s
from app.db import connect
from app.evaluation import score_run, synthetic_fixture
from app.ingest_pipeline import ingest
from app.models import MemoryEntry
from app.search import hybrid_search
from tests.test_evaluation import HashedBagOfWords

BASELINE = {"recall@10": 1.0, "mrr": 0.9}  # Task 8, per-project questions


@pytest.fixture
def brain(tmp_db, monkeypatch):
    monkeypatch.setattr(s, "_provider", HashedBagOfWords())
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    return tmp_db


async def _add(content, type_="note", project="acme", age_days=0, **kw):
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    entry = await ingest(MemoryEntry(content=content, type=type_, project=project,
                                     timestamp=ts, importance=kw.pop("importance", 3), **kw))
    return entry.id


async def _ids(query, db, **kw):
    return [r["id"] for r in await hybrid_search(query, limit=10, db_path=db, **kw)]


@pytest.mark.asyncio
async def test_a_natural_question_gets_keyword_hits(brain):
    target = await _add("Kafka consumer lag alert tuned: raised threshold to 5000", "fact")
    await _add("Lunch order for the team offsite", "note")
    results = await hybrid_search("how did we fix the kafka lag alert", db_path=brain)
    top = results[0]
    assert top["id"] == target and top["matched"] in ("keyword", "both")
    assert isinstance(top["score"], float) and top["excerpt"]


@pytest.mark.asyncio
async def test_only_stopwords_is_not_an_error(brain):
    await _add("anything at all", "note")
    assert isinstance(await hybrid_search("how did we", db_path=brain), list)


@pytest.mark.asyncio
async def test_the_tail_of_a_long_session_is_findable(brain):
    answer = " Finally the certificate on the proxy had expired, renewing it fixed the outage."
    filler = "Checked the dashboards and the deploy history again with no change. " * 300
    body = filler[: 20_000 - len(answer)] + answer  # 20,000 characters, answer at the very end
    assert len(body) == 20_000
    session = await _add(body, "session")
    await _add("Proxy settings are managed by the platform team", "note")
    results = await hybrid_search("expired certificate on the proxy", db_path=brain)
    hit = next(r for r in results if r["id"] == session)
    assert "certificate" in hit["excerpt"] and len(hit["excerpt"]) <= 400


@pytest.mark.asyncio
async def test_an_old_exact_fact_beats_a_fresh_loose_session(brain):
    fact = await _add("The billing database backup runs at 03:00 UTC", "fact", age_days=90)
    await _add("Session: talked about billing and some backup ideas for later", "session")
    assert (await _ids("when does the billing database backup run", brain))[0] == fact


@pytest.mark.asyncio
async def test_seven_copies_of_a_session_do_not_fill_the_page(brain):
    for i in range(7):
        await _add(f"Session {i}: rebuilt the export pipeline and checked the queue", "session")
    await _add("The export pipeline writes to the reports share", "fact")
    results = await hybrid_search("export pipeline queue", limit=10, db_path=brain)
    sessions = [r for r in results if r["type"] in ("session", "handover")]
    assert len(sessions) <= 2 and any(r["type"] == "fact" for r in results)


@pytest.mark.asyncio
async def test_as_of_returns_the_fact_valid_at_that_time(brain):
    old = await _add("The API gateway runs on port 8080", "fact", age_days=30)
    conn = connect(brain)
    try:
        with conn:
            conn.execute("UPDATE memories SET valid_from = timestamp WHERE id = ?", (old,))
    finally:
        conn.close()
    new = await _add("The API gateway runs on port 9090 since the move", "fact")
    conn = connect(brain)
    try:
        with conn:  # close the old fact the way supersession does
            now = datetime.now(timezone.utc).isoformat()
            conn.execute("UPDATE memories SET status = 'archived', valid_to = ?, "
                         "superseded_by = ?, invalidated_by = ? WHERE id = ?",
                         (now, new, new, old))
    finally:
        conn.close()
    before = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert await _ids("API gateway port", brain, as_of=before) == [old]
    current = await _ids("API gateway port", brain)
    assert new in current and old not in current


@pytest.mark.asyncio
async def test_feedback_from_an_unrelated_question_does_not_boost(brain):
    a = await _add("Printer on floor two needs toner", "note")
    b = await _add("Printer driver for the plotter", "note")
    conn = connect(brain)
    try:  # b was chosen often, but for a question about lunch (events only, no recall boost)
        with conn:
            for n in range(5):
                conn.execute("INSERT INTO retrieval_events (id, project, query, result_ids, "
                             "chosen_id, source, created_at) VALUES (?, NULL, ?, ?, ?, 't', ?)",
                             (f"ev{n}", "lunch menu friday", f'["{b}"]', b,
                              datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()
    from app.retrieval import feedback_boosts
    assert feedback_boosts([a, b], db_path=brain, query_terms={"printer", "toner"}) == {}
    assert feedback_boosts([a, b], db_path=brain, query_terms={"lunch"})[b] > 1.0
    first = await hybrid_search("printer toner", db_path=brain)
    assert first[0]["id"] == a


async def _synthetic(brain, cross_project: bool):
    memories, labels = synthetic_fixture()
    for m in memories:
        await ingest(MemoryEntry(id=m["id"], content=m["content"], type=m["type"],
                                 project=m["project"], importance=3))
    results, wanted = {}, {}
    for label in labels:
        project = None if cross_project else label["project"]
        hits = await hybrid_search(label["query"], limit=10, project=project, db_path=brain)
        results[label["query"]] = [h["id"] for h in hits]
        wanted[label["query"]] = set(label["relevant"])
    return score_run(results, wanted)


@pytest.mark.asyncio
async def test_synthetic_questions_meet_the_bar(brain, capsys):
    report = await _synthetic(brain, cross_project=False)
    print(f"\nSEARCH V3 per-project recall@5={report['recall@5']} "
          f"recall@10={report['recall@10']} mrr={report['mrr']} (baseline {BASELINE})")
    # recall@10 cannot fail here (10 memories per project, limit 10: the
    # fixture size is the plan's), so MRR and recall@5 carry the check.
    assert report["recall@5"] >= 0.95 and report["mrr"] >= 0.95


@pytest.mark.asyncio
async def test_synthetic_questions_across_all_projects(brain, capsys):
    report = await _synthetic(brain, cross_project=True)
    print(f"\nSEARCH V3 all-projects recall@5={report['recall@5']} "
          f"recall@10={report['recall@10']} mrr={report['mrr']}")
    assert report["recall@10"] >= 0.85
    assert report["recall@5"] >= 0.95 and report["mrr"] >= 0.95


# ------------------------------------------------------------- review follow-ups

@pytest.mark.asyncio
async def test_a_bad_as_of_is_refused_not_ignored(brain, monkeypatch):
    import json
    from fastapi.testclient import TestClient
    from app.main import app
    from app.mcp.tools import handle_search_memory
    from app.search import search_with_status
    await _add("The export runs nightly.", "fact")
    with pytest.raises(ValueError):
        await search_with_status("export", as_of="yesterday", db_path=brain)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", brain)
    reply = json.loads(await handle_search_memory("export", as_of="2026-13-40"))
    assert "as_of" in reply["error"]
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.main.DB_PATH", brain)
    r = TestClient(app, base_url="http://localhost:7741").get(
        "/search", params={"q": "export", "as_of": "yesterday"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_as_of_finds_an_old_match_behind_thirty_newer_ones(brain):
    old = await _add("The export runs at two.", "fact", age_days=30)
    for i in range(40):
        await _add(f"export export export job {i}", "fact")
    as_of = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat()
    assert old in await _ids("export", brain, as_of=as_of)


@pytest.mark.asyncio
async def test_as_of_never_brings_back_a_deleted_memory(brain):
    from app.storage import archive_memory_audited
    gone = await _add("The gateway runs on SRVDB01.", "fact", age_days=10)
    archive_memory_audited(gone, actor="ui", reason="wrong", db_path=brain)
    assert gone not in await _ids("gateway", brain, as_of=datetime.now(timezone.utc).isoformat())


@pytest.mark.asyncio
async def test_a_session_type_filter_is_not_capped(brain):
    for i in range(5):
        await _add(f"Session {i}: rebuilt the export pipeline", "session")
    results = await hybrid_search("export pipeline", limit=10, type_filter="session",
                                  project="acme", db_path=brain)
    assert len(results) == 5


@pytest.mark.asyncio
async def test_a_strong_memory_outranks_a_weak_twin(brain):
    weak = await _add("The invoice export job retries twice.", "fact")
    strong = await _add("The invoice export job retries two times.", "fact")
    conn = connect(brain)
    try:
        with conn:
            conn.execute("UPDATE memories SET strength = 0.3 WHERE id = ?", (weak,))
            conn.execute("UPDATE memories SET strength = 2.5 WHERE id = ?", (strong,))
    finally:
        conn.close()
    order = await _ids("invoice export job retries", brain)
    assert order.index(strong) < order.index(weak)


def test_the_age_curve_halves_every_45_days_down_to_a_floor():
    from app.search import age_factor
    now = datetime.now(timezone.utc)
    assert age_factor("session", now.isoformat()) == pytest.approx(1.0, abs=1e-3)
    assert age_factor("session", (now - timedelta(days=45)).isoformat()) == pytest.approx(0.5, abs=1e-3)
    assert age_factor("note", (now - timedelta(days=400)).isoformat()) == 0.5
    assert age_factor("fact", (now - timedelta(days=400)).isoformat()) == 1.0


def test_chunk_vectors_compete_and_name_their_span(tmp_db):
    from app.models import MemoryEntry
    from app.storage import add_memory
    from app.vector import insert_vectors, vec_search_multi
    entry = MemoryEntry(content="x" * 1800, type="session", project="acme")
    add_memory(entry, db_path=tmp_db)
    conn = connect(tmp_db, vec=True)
    try:
        with conn:
            insert_vectors(conn, entry.id, [1.0, 0.0, 0.0, 0.0], "m",
                           [(0, 0, 900, [0.0, 1.0, 0.0, 0.0]), (1, 900, 1800, [0.0, 0.0, 1.0, 0.0])])
    finally:
        conn.close()
    (hit,) = vec_search_multi({"m": [0.0, 0.0, 1.0, 0.0]}, n_results=5, db_path=tmp_db,
                              include_chunks=True)
    assert hit["id"] == entry.id and hit["chunk"] == {"start": 900, "end": 1800}
    (whole,) = vec_search_multi({"m": [0.0, 0.0, 1.0, 0.0]}, n_results=5, db_path=tmp_db)
    assert "chunk" not in whole
