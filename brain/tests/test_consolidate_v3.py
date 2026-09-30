"""v3 knowledge upkeep: time-based decay, one run at a time, cited beliefs that
wait for approval, judged conflicts, open loops that close."""
import math
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import app.consolidate as cons
import app.summarise as s
from app.db import connect
from app.linker import _write_edges
from app.models import MemoryEntry
from app.storage import add_memory, effective_strength, get_memory, set_meta
from app.vector import vec_add
from tests.test_evaluation import HashedBagOfWords

NOW = datetime.now(timezone.utc)


class FakeModel(HashedBagOfWords):
    """Hashed embeddings plus scripted answers for the raw prompts."""

    def __init__(self, answers=()):
        super().__init__()
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.answers.pop(0) if self.answers else "NO"


@pytest.fixture
def model(tmp_db, monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(s, "_provider", fake)
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.delenv("MEMORYBRAIN_JUDGE", raising=False)
    return fake


def _mem(db, content, type_="note", project="acme", age_days=0, **kw):
    entry = MemoryEntry(content=content, type=type_, project=project, importance=3,
                        timestamp=NOW - timedelta(days=age_days), **kw)
    add_memory(entry, db_path=db)
    return entry.id


def _strengths(db):
    conn = connect(db)
    try:
        return {r["id"]: r["strength"] for r in conn.execute("SELECT id, strength FROM memories")}
    finally:
        conn.close()


# ------------------------------------------------------------- decay (D8)

@pytest.mark.asyncio
async def test_two_runs_in_a_row_change_no_strength(tmp_db, model):
    for i in range(4):
        _mem(tmp_db, f"note number {i} about the car", age_days=100)
    before = _strengths(tmp_db)
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    assert _strengths(tmp_db) == before


def test_effective_strength_halves_after_sixty_idle_days_past_grace():
    ts = (NOW - timedelta(days=74)).isoformat()
    assert effective_strength(1.0, ts, None, pinned=False) == pytest.approx(0.5, abs=0.01)
    assert effective_strength(1.0, ts, None, pinned=True) == 1.0
    recent = (NOW - timedelta(days=5)).isoformat()
    assert effective_strength(1.0, ts, recent, pinned=False) == 1.0  # recalled lately
    assert effective_strength(0.1, (NOW - timedelta(days=900)).isoformat(), None, False) == 0.2


# ------------------------------------------------------------- one run at a time (W10)

@pytest.mark.asyncio
async def test_a_second_run_while_one_runs_is_skipped(tmp_db, model):
    async with cons.RUN_LOCK:
        assert await cons.consolidate(project="acme", db_path=tmp_db) == {"skipped": "already running"}


@pytest.mark.asyncio
async def test_a_fresh_marker_blocks_and_a_stale_one_does_not(tmp_db, model):
    set_meta(cons.META_RUNNING, (NOW - timedelta(minutes=10)).isoformat(), db_path=tmp_db)
    assert await cons.consolidate(project="acme", db_path=tmp_db) == {"skipped": "already running"}
    set_meta(cons.META_RUNNING, (NOW - timedelta(hours=3)).isoformat(), db_path=tmp_db)
    report = await cons.consolidate(project="acme", db_path=tmp_db)
    assert "projects" in report
    from app.storage import get_meta
    assert get_meta(cons.META_RUNNING, db_path=tmp_db) == ""
    assert get_meta("consolidation_last_run:acme", db_path=tmp_db)


# ------------------------------------------------------------- cited beliefs (I4)

def _cluster(db, texts):
    ids = [_mem(db, t, type_="fact") for t in texts]
    _write_edges([{"src": a, "dst": b, "kind": "semantic", "weight": 0.9, "directed": 0,
                   "meta": {}} for a in ids for b in ids if a < b], db)
    return ids


def test_only_sentences_citing_real_sources_survive():
    tags = {"aaaa1111", "bbbb2222"}
    text = ("The export runs nightly [m:aaaa1111]. It is great. "
            "Invoices go out on the first [m:bbbb2222]. Made up [m:zzzz9999].")
    assert cons.cited_sentences(text, tags) == [
        "The export runs nightly [m:aaaa1111].", "Invoices go out on the first [m:bbbb2222]."]


@pytest.mark.asyncio
async def test_a_belief_is_cited_proposed_and_waits_for_approval(tmp_db, model, monkeypatch):
    from app.brief import build_project_brief
    from app.storage import set_belief_status

    long = " with enough detail to need a real distillation step" * 4
    ids = _cluster(tmp_db, [f"The export job runs nightly at 02:00{long}",
                            f"The export job writes one file per customer{long}",
                            f"The export job retries twice on failure{long}"])
    model.answers = [f"The export runs nightly and retries twice [m:{ids[0][:8]}]. "
                     "Everyone loves it."]
    report = await cons.consolidate(project="acme", mode="full", db_path=tmp_db)
    belief_id = report["projects"][0]["beliefs"][0]["id"]
    belief = get_memory(belief_id, db_path=tmp_db)
    assert belief.content == f"The export runs nightly and retries twice [m:{ids[0][:8]}]."
    assert (belief.status, belief.trust, belief.writer) == ("proposed", "derived", "consolidation")
    assert "[m:" in model.prompts[0] and ids[0][:8] in model.prompts[0]
    brief = await build_project_brief("acme", max_chars=12000, db_path=tmp_db)
    assert belief_id not in [b["id"] for b in brief["beliefs"]]
    assert set_belief_status(belief_id, approve=True, actor="user", db_path=tmp_db)
    brief = await build_project_brief("acme", max_chars=12000, db_path=tmp_db)
    assert belief_id in [b["id"] for b in brief["beliefs"]]


@pytest.mark.asyncio
async def test_a_belief_with_no_cited_sentence_is_skipped(tmp_db, model):
    long = " with enough detail to need a real distillation step" * 4
    _cluster(tmp_db, [f"Fact one{long}", f"Fact two{long}", f"Fact three{long}"])
    model.answers = ["A summary that cites nothing at all."]
    report = await cons.consolidate(project="acme", mode="full", db_path=tmp_db)
    assert report["projects"][0]["beliefs"] == []


def test_the_ui_approves_and_rejects_proposed_beliefs(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ui.queries.DB_PATH", tmp_db)
    keep = _mem(tmp_db, "Belief to keep [m:x].", type_="belief", status="proposed")
    drop = _mem(tmp_db, "Belief to drop [m:y].", type_="belief", status="proposed")
    client = TestClient(app, headers={"X-Brain-Client": "atlas"})
    listed = client.get("/api/ui/beliefs", params={"status": "proposed"}).json()
    assert {b["id"] for b in listed["beliefs"]} == {keep, drop}
    assert client.post(f"/api/ui/edit/beliefs/{keep}/approve").status_code == 200
    assert client.post(f"/api/ui/edit/beliefs/{drop}/reject").status_code == 200
    assert get_memory(keep, db_path=tmp_db).status == "active"
    assert get_memory(drop, db_path=tmp_db).status == "archived"


# ------------------------------------------------------------- judged conflicts (I5)

def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _two_facts_at(db, cos):
    a = _mem(db, "The gateway listens on port 8080", type_="fact")
    b = _mem(db, "The gateway listens on port 9090", type_="fact")
    va = [1.0] + [0.0] * 7
    vb = _unit([cos] + [math.sqrt(1 - cos * cos)] + [0.0] * 6)
    vec_add(a, va, {}, db_path=db, model=s.embed_model_id())
    vec_add(b, vb, {}, db_path=db, model=s.embed_model_id())
    return a, b


def _conflict_edges(db):
    conn = connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM memory_links WHERE kind = 'conflicts_with'"
                            ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,edges", [("NO", 0), ("YES, they contradict", 1)])
async def test_the_judge_decides_which_pairs_conflict(tmp_db, model, monkeypatch, answer, edges):
    monkeypatch.setenv("MEMORYBRAIN_JUDGE", "on")
    _two_facts_at(tmp_db, 0.90)
    model.answers = [answer]
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    assert _conflict_edges(tmp_db) == edges
    assert "contradict" in model.prompts[0].lower()


@pytest.mark.asyncio
async def test_without_the_judge_similar_facts_are_flagged_as_before(tmp_db, model):
    _two_facts_at(tmp_db, 0.90)
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    assert _conflict_edges(tmp_db) == 1 and model.prompts == []


# ------------------------------------------------------------- open loops (I6)

def _loops(db):
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, content, type, status, tags, trust, writer, valid_to, invalidated_by "
            "FROM memories WHERE type = 'open_loop'")]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_loops_are_extracted_as_open_loops_and_negations_are_skipped(tmp_db, model):
    _mem(tmp_db, "Session: shipped the export.\nNo follow-up needed on the export.\n"
                 "Nothing unresolved on billing.\nTODO: rotate the backup encryption key\n",
         type_="session", age_days=2)
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    loops = _loops(tmp_db)
    assert [l["content"] for l in loops] == ["Open loop: TODO: rotate the backup encryption key"]
    assert (loops[0]["type"], loops[0]["trust"], loops[0]["writer"]) == \
        ("open_loop", "derived", "consolidation")
    assert '"open_loop"' in loops[0]["tags"]


@pytest.mark.asyncio
async def test_a_loop_closes_when_a_later_session_says_it_is_done(tmp_db, model):
    from app.brief import build_project_brief

    _mem(tmp_db, "Session one.\nTODO: rotate the backup encryption key\n", type_="session",
         age_days=3)
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    later = _mem(tmp_db, "Session two. We rotated the backup encryption key and it is fixed now.",
                 type_="session", age_days=1)
    await cons.consolidate(project="acme", mode="light", db_path=tmp_db)
    (loop,) = _loops(tmp_db)
    assert loop["status"] == "done" and loop["invalidated_by"] == later and loop["valid_to"]
    brief = await build_project_brief("acme", db_path=tmp_db)
    assert loop["id"] not in [l["id"] for l in brief["open_loops"]]


def test_migration_010_converts_old_open_loop_notes(tmp_path):
    from app.migrations.runner import MIGRATIONS_DIR, run_migrations
    from tests.test_migrations import _make_minimal_db

    db = tmp_path / "brain.db"
    _make_minimal_db(db)
    migs = tmp_path / "pre010"
    migs.mkdir()
    for mf in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if mf.name < "010":
            shutil.copy(mf, migs / mf.name)
    run_migrations(db_path=db, migrations_dir=migs)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO memories (id, content, type, project, tags, timestamp) VALUES "
                     "('l1', 'Open loop: fix it', 'note', 'acme', '[\"open-loop\"]', '2026-01-01')")
        conn.commit()
    run_migrations(db_path=db)
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT type, tags FROM memories WHERE id = 'l1'").fetchone()
    assert row == ("open_loop", '["open_loop"]')
