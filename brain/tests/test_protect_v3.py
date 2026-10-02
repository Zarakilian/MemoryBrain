"""v3: user verdicts survive repairs, deletes are audited and reversible,
excerpts, degraded search, next-session scoping, small fixes."""
import json

import pytest

from app.db import connect
from app.linker import _write_edges, rebuild_graph
from app.models import MemoryEntry, Project
from app.storage import (add_memory, archive_memory_audited, get_memory, get_project,
                         hard_delete_memory, restore_memory, upsert_project)
from app.vector import vec_add


def _mem(db, content, type_="note", project="acme", tags=None, vector=None):
    entry = MemoryEntry(content=content, type=type_, project=project, tags=tags or [],
                        importance=3)
    add_memory(entry, db_path=db)
    if vector is not None:
        vec_add(entry.id, vector, {}, db_path=db)
    return entry.id


def _edges(db, memory_id=None):
    conn = connect(db)
    try:
        sql = "SELECT src_id, dst_id, kind, meta FROM memory_links"
        rows = conn.execute(sql + (" WHERE src_id = ? OR dst_id = ?" if memory_id else ""),
                            (memory_id, memory_id) if memory_id else ()).fetchall()
        return {(r["src_id"], r["dst_id"], r["kind"]): json.loads(r["meta"]) for r in rows}
    finally:
        conn.close()


def _audit_rows(db, memory_id):
    conn = connect(db)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT action, actor, reason FROM memory_audit WHERE memory_id = ? ORDER BY at",
            (memory_id,))]
    finally:
        conn.close()


def _verdicts(db):
    """A belief citing two sources, and a dismissed contradiction between them."""
    a = _mem(db, "the car is red", vector=[1.0, 0.0, 0.0, 0.0])
    b = _mem(db, "the car is blue", vector=[1.0, 0.0, 0.0, 0.0])
    belief = _mem(db, "the car colour is disputed", type_="belief")
    lo, hi = sorted([a, b])
    _write_edges([
        {"src": belief, "dst": a, "kind": "derived_from", "weight": 1.0, "directed": 1, "meta": {}},
        {"src": belief, "dst": b, "kind": "derived_from", "weight": 1.0, "directed": 1, "meta": {}},
        {"src": lo, "dst": hi, "kind": "conflicts_with", "weight": 0.01, "directed": 0,
         "meta": {"dismissed": True}},
    ], db)
    return a, b, belief, (lo, hi)


# ------------------------------------------------------------- D2, W8

def test_rebuild_graph_keeps_citations_and_dismissed_conflicts(tmp_db):
    a, b, belief, (lo, hi) = _verdicts(tmp_db)
    rebuild_graph(db_path=tmp_db)
    edges = _edges(tmp_db)
    assert (belief, a, "derived_from") in edges and (belief, b, "derived_from") in edges
    assert edges[(lo, hi, "conflicts_with")] == {"dismissed": True}
    assert (lo, hi, "semantic") in edges  # derived edges are rebuilt


def test_ui_content_edit_keeps_verdicts_and_rehashes(tmp_db, fake_provider, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import content_hash

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    a, b, belief, (lo, hi) = _verdicts(tmp_db)
    r = TestClient(app).patch(f"/api/ui/edit/memories/{a}", json={"content": "the car is crimson"})
    assert r.status_code == 200
    edges = _edges(tmp_db, a)
    assert (belief, a, "derived_from") in edges
    assert edges[(lo, hi, "conflicts_with")] == {"dismissed": True}
    conn = connect(tmp_db)
    try:
        row = conn.execute("SELECT content_hash, content_updated_at, embedded FROM memories "
                           "WHERE id = ?", (a,)).fetchone()
    finally:
        conn.close()
    assert row["content_hash"] == content_hash("the car is crimson", "acme")
    assert row["content_updated_at"] and row["embedded"] == 1


def test_ui_add_note_rejects_an_oversized_fact(tmp_db, fake_provider, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    upsert_project(Project(slug="acme", name="Acme"), db_path=tmp_db)
    r = TestClient(app).post("/api/ui/edit/notes",
                             json={"content": "x" * 4001, "project": "acme", "type": "fact"})
    assert r.status_code == 422


# ------------------------------------------------------------- S2

@pytest.mark.asyncio
async def test_mcp_delete_archives_with_an_audit_row_and_restore_reverses_it(tmp_db, monkeypatch):
    from app.mcp.tools import handle_delete_memory

    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    mid = _mem(tmp_db, "wrong entry")
    reply = json.loads(await handle_delete_memory(mid, reason="typo"))
    assert reply == {"archived": True, "id": mid, "restore": "brain_admin restore_memory"}
    assert get_memory(mid, db_path=tmp_db).status == "archived"
    assert restore_memory(mid, actor="user", db_path=tmp_db) is True
    assert get_memory(mid, db_path=tmp_db).status == "active"
    rows = _audit_rows(tmp_db, mid)
    assert [r["action"] for r in rows] == ["archive", "restore"]
    assert rows[0]["actor"].startswith("mcp") and rows[0]["reason"] == "typo"


def test_archive_and_restore_report_missing_rows(tmp_db):
    assert archive_memory_audited("nope", actor="user", db_path=tmp_db) is False
    assert restore_memory("nope", actor="user", db_path=tmp_db) is False


def test_hard_delete_removes_everything_but_the_audit_row(tmp_db):
    from app.pins import pin_memory

    upsert_project(Project(slug="acme", name="Acme"), db_path=tmp_db)
    a, b, belief, _ = _verdicts(tmp_db)
    pin_memory("acme", a, db_path=tmp_db)
    conn = connect(tmp_db)
    try:
        with conn:
            conn.execute("INSERT INTO vec_chunks (memory_id, chunk_ix, start_char, end_char, "
                         "model, dim, embedding) VALUES (?, 0, 0, 5, 'm', 1, x'00000000')", (a,))
            conn.execute("INSERT INTO file_links (src_kind, src_id, dst_kind, dst_id, kind, "
                         "created_at) VALUES ('memory', ?, 'dangling', 'x.py', 'file_ref', 'now')",
                         (a,))
    finally:
        conn.close()
    assert hard_delete_memory(a, actor="ui", reason="wrong", db_path=tmp_db) is True
    conn = connect(tmp_db)
    try:
        for table, col in (("memories", "id"), ("vec_memories", "memory_id"),
                           ("vec_chunks", "memory_id"), ("project_pins", "memory_id"),
                           ("file_links", "src_id")):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?",
                                (a,)).fetchone()[0] == 0, table
    finally:
        conn.close()
    assert _edges(tmp_db, a) == {}
    assert [r["action"] for r in _audit_rows(tmp_db, a)] == ["hard_delete"]


# ------------------------------------------------------------- C4

@pytest.mark.asyncio
async def test_get_memory_excerpts(tmp_db, fake_provider, monkeypatch):
    from app.ingest_pipeline import ingest
    from app.mcp.tools import handle_get_memory

    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    body = ("Filler about nothing in particular. " * 150 + "The invoice export failed on "
            "the last day of the month. " + "More filler text here. " * 150)
    mid = (await ingest(MemoryEntry(content=body, type="session", project="acme"))).id

    around = json.loads(await handle_get_memory(mid, around="invoice export failed"))
    assert "invoice export failed" in around["content"]
    assert len(around["content"]) < len(body) and around["content_chars"] == len(body)
    assert around["truncated"] is True

    short = json.loads(await handle_get_memory(mid, max_chars=100))
    assert short["content"] == body[:100] and short["truncated"] is True

    full = json.loads(await handle_get_memory(mid))
    assert full["content"] == body and full.get("truncated", False) is False


# ------------------------------------------------------------- R7

@pytest.mark.asyncio
async def test_search_degrades_to_keywords_when_embedding_fails(tmp_db, fake_provider, monkeypatch):
    from app.mcp.tools import handle_search_memory

    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    monkeypatch.setattr("app.search.DB_PATH", tmp_db)
    mid = _mem(tmp_db, "the quarterly invoice export")
    fake_provider.fail_on = {"query:"}  # every query embedding fails
    reply = json.loads(await handle_search_memory("invoice"))
    assert reply["degraded"] == "semantic search unavailable"
    assert mid in [r["id"] for r in reply["results"]]


# ------------------------------------------------------------- W2, O5

def test_next_session_note_is_scoped_to_the_project(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.main.DB_PATH", tmp_db)
    other = MemoryEntry(content="other project note", type="note", project="side-project",
                        tags=["next_session"], writer="grok")
    add_memory(other, db_path=tmp_db)
    old = MemoryEntry(content="archived acme note", type="note", project="acme",
                      tags=["next_session"], status="archived")
    add_memory(old, db_path=tmp_db)
    mine = MemoryEntry(content="acme: start with the export", type="note", project="acme",
                       tags=["next_session"], writer="claude")
    add_memory(mine, db_path=tmp_db)
    client = TestClient(app)

    assert client.get("/next-session").json()["notes"] == ""
    data = client.get("/next-session", params={"project": "acme"}).json()
    assert data["notes"] == "acme: start with the export"
    assert data["id"] == mine.id and data["writer"] == "claude" and data["timestamp"]


def test_upsert_project_keeps_an_existing_one_liner(tmp_db):
    upsert_project(Project(slug="acme", name="Acme", one_liner="Invoices and exports"),
                   db_path=tmp_db)
    upsert_project(Project(slug="acme", name="Acme"), db_path=tmp_db)
    assert get_project("acme", db_path=tmp_db).one_liner == "Invoices and exports"


# ------------------------------------------------------------- review follow-ups

def test_an_edit_while_the_model_is_down_drops_stale_vectors_and_queues_a_retry(
        tmp_db, fake_provider, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.reembed import pending_count

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    mid = _mem(tmp_db, "the car is red", vector=[1.0, 0.0, 0.0, 0.0])
    fake_provider.fail_on = {"title: none"}  # document embedding is down
    r = TestClient(app).patch(f"/api/ui/edit/memories/{mid}", json={"content": "the car is blue"})
    assert r.status_code == 200 and r.json()["relinked"] is False
    conn = connect(tmp_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM vec_memories WHERE memory_id = ?",
                            (mid,)).fetchone()[0] == 0
        assert conn.execute("SELECT embedded FROM memories WHERE id = ?", (mid,)).fetchone()[0] == 0
    finally:
        conn.close()
    assert pending_count(db_path=tmp_db) >= 1


def test_a_flagged_memory_with_a_current_vector_is_still_pending(tmp_db, fake_provider):
    import app.summarise as s
    from app.reembed import pending_count
    mid = _mem(tmp_db, "flagged")
    vec_add(mid, [0.3] * 8, {}, db_path=tmp_db, model=s.embed_model_id())
    conn = connect(tmp_db)
    try:
        with conn:
            conn.execute("UPDATE memories SET embedded = 0 WHERE id = ?", (mid,))
    finally:
        conn.close()
    assert pending_count(db_path=tmp_db) == 1


def test_resolving_a_conflict_audits_the_archive(tmp_db):
    from app.conflicts import resolve_conflict
    a, b, belief, (lo, hi) = _verdicts(tmp_db)
    conn = connect(tmp_db)
    try:
        with conn:  # make it a live contradiction again
            conn.execute("UPDATE memory_links SET weight = 0.9, meta = '{}' "
                         "WHERE kind = 'conflicts_with'")
    finally:
        conn.close()
    assert "error" not in resolve_conflict(lo, hi, db_path=tmp_db)  # lo wins, hi is archived
    rows = _audit_rows(tmp_db, hi)
    assert [r["action"] for r in rows] == ["archive"] and rows[0]["actor"] == "resolve_conflict"


@pytest.mark.asyncio
async def test_supersession_is_audited(tmp_db, fake_provider, monkeypatch):
    import app.summarise as s
    from app.ingest_pipeline import ingest

    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    vector = await s.embed_document("The car is red.")
    old = MemoryEntry(content="The car is green.", type="fact", project="acme", importance=4)
    add_memory(old, db_path=tmp_db)
    vec_add(old.id, vector, {}, db_path=tmp_db, model=s.embed_model_id())
    new = await ingest(MemoryEntry(content="The car is red.", type="fact", project="acme",
                                   writer="claude"))
    rows = _audit_rows(tmp_db, old.id)
    assert [r["action"] for r in rows] == ["supersede"] and rows[0]["actor"] == "claude"
    assert new.superseded == [old.id]


def test_restore_reopens_the_validity_window(tmp_db):
    mid = _mem(tmp_db, "a fact that came back", type_="fact")
    conn = connect(tmp_db)
    try:
        with conn:
            conn.execute("UPDATE memories SET status = 'archived', superseded_by = 'x', "
                         "invalidated_by = 'x', valid_to = '2026-09-01T00:00:00+00:00' "
                         "WHERE id = ?", (mid,))
    finally:
        conn.close()
    assert restore_memory(mid, actor="ui", db_path=tmp_db)
    got = get_memory(mid, db_path=tmp_db)
    assert (got.status, got.superseded_by, got.invalidated_by, got.valid_to) == \
        ("active", None, None, None)


@pytest.mark.asyncio
async def test_an_atlas_edit_indexes_off_the_event_loop(tmp_db, fake_provider, monkeypatch):
    """W5: entity and path extraction on a long edit must not freeze every
    other client while it runs."""
    import asyncio
    import time
    from app.ui import editor

    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    mid = _mem(tmp_db, "docs/a.md and src/b.py", vector=[0.1] * 768)

    def slow_index(*a, **k):
        time.sleep(0.6)
        return 0
    monkeypatch.setattr("app.entities.index_entities", slow_index)
    monkeypatch.setattr("app.linker.link_new_memory", lambda *a, **k: time.sleep(0.6))

    gaps, done = [], asyncio.Event()

    async def ticker():
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    tick = asyncio.create_task(ticker())
    await editor._reindex_after_edit(mid, content_changed=True)
    done.set()
    await tick
    assert max(gaps) < 0.3, f"event loop stalled {max(gaps):.2f}s"


def test_a_rebuild_chains_sessions_across_an_archived_one(tmp_db):
    """D3 chains sessions instead of archiving them. A 2.x brain still holds
    sessions it archived; a rebuild must link around them, not drop the chain."""
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    ids = []
    for i in range(3):
        entry = MemoryEntry(content=f"session {i}", type="session", project="acme",
                            importance=3, timestamp=base + timedelta(days=i))
        add_memory(entry, db_path=tmp_db)
        ids.append(entry.id)
    s1, s2, s3 = ids
    assert archive_memory_audited(s2, actor="ui", db_path=tmp_db)
    rebuild_graph(tmp_db)
    chain = {(k[0], k[1]) for k in _edges(tmp_db) if k[2] == "session_chain"}
    assert chain == {(s3, s1)}


def test_an_edit_cut_off_before_its_reindex_still_queues_a_re_embed(
        tmp_db, fake_provider, monkeypatch):
    """The new text is committed first. If the re-index never finishes (a
    restart, a lock, a hung model), the old vector must not pass as current."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.reembed import pending_count

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    mid = _mem(tmp_db, "the car is red", vector=[1.0, 0.0, 0.0, 0.0])

    async def cut_off(*a, **k):
        raise RuntimeError("process stopped")
    monkeypatch.setattr("app.ui.editor._reindex_after_edit", cut_off)
    with pytest.raises(RuntimeError):
        TestClient(app).patch(f"/api/ui/edit/memories/{mid}", json={"content": "the car is blue"})
    conn = connect(tmp_db)
    try:
        row = conn.execute("SELECT content, embedded FROM memories WHERE id = ?", (mid,)).fetchone()
    finally:
        conn.close()
    assert row["content"] == "the car is blue" and row["embedded"] == 0
    assert pending_count(db_path=tmp_db) >= 1


@pytest.mark.asyncio
async def test_search_hits_and_get_memory_say_who_wrote_them(tmp_db, fake_provider, monkeypatch):
    """AGENTS.md: every item in a brief or search result carries trust and writer."""
    from app.ingest_pipeline import ingest
    from app.mcp.tools import handle_get_memory, handle_search_memory

    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.search.DB_PATH", tmp_db, raising=False)
    mid = (await ingest(MemoryEntry(content="the invoice export runs nightly", type="note",
                                    project="acme", writer="grok"))).id
    hits = json.loads(await handle_search_memory("invoice export", project="acme"))
    hits = hits["results"] if isinstance(hits, dict) else hits
    hit = next(h for h in hits if h["id"] == mid)
    assert hit["trust"] == "agent" and hit["writer"] == "grok"
    got = json.loads(await handle_get_memory(mid))
    assert got["trust"] == "agent" and got["writer"] == "grok"


# ------------------------------------------------------------- Atlas edits (W8 rest)

def _atlas(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    monkeypatch.setattr("app.ui.editor.DB_PATH", tmp_db)
    return TestClient(app)


def test_an_atlas_edit_meets_the_same_limits_as_a_write(tmp_db, fake_provider, monkeypatch):
    client = _atlas(tmp_db, monkeypatch)
    fact = _mem(tmp_db, "The port is 7741.", type_="fact")
    other = _mem(tmp_db, "Something else entirely.")
    patch = lambda body: client.patch(f"/api/ui/edit/memories/{fact}", json=body)
    assert patch({"content": "x" * 5000}).status_code == 422          # a fact is short
    assert patch({"tags": [f"t{i}" for i in range(21)]}).status_code == 422
    assert patch({"tags": ["t" * 101]}).status_code == 422
    assert patch({"content": "Something else entirely."}).status_code == 409
    assert get_memory(fact, db_path=tmp_db).content == "The port is 7741."
    assert patch({"content": "The port is 7742."}).status_code == 200


def test_atlas_tag_edits_do_not_inflate_tag_counts(tmp_db, fake_provider, monkeypatch):
    client = _atlas(tmp_db, monkeypatch)
    a = _mem(tmp_db, "first note", tags=["alpha"], vector=[1.0, 0.0, 0.0, 0.0])
    _mem(tmp_db, "second note", tags=["alpha"], vector=[0.9, 0.1, 0.0, 0.0])
    rebuild_graph(tmp_db)
    for tags in (["alpha", "beta"], ["alpha"], ["alpha", "gamma"]):
        assert client.patch(f"/api/ui/edit/memories/{a}", json={"tags": tags}).status_code == 200
    conn = connect(tmp_db)
    try:
        df = dict(conn.execute("SELECT tag, df FROM tag_stats").fetchall())
    finally:
        conn.close()
    assert df["alpha"] == 2 and df.get("beta", 0) == 0 and df["gamma"] == 1


def test_a_hard_delete_leaves_no_row_pointing_at_the_deleted_memory(tmp_db):
    """Deleting the newer of two memories left the older one archived with
    superseded_by naming a row that no longer exists."""
    old = _mem(tmp_db, "The port is 7741.", type_="fact")
    new = _mem(tmp_db, "The port is 7742.", type_="fact")
    conn = connect(tmp_db)
    try:
        conn.execute("UPDATE memories SET status = 'archived', superseded_by = ?, "
                     "invalidated_by = ?, valid_to = '2026-09-30T00:00:00+00:00' WHERE id = ?",
                     (new, new, old))
        conn.execute("UPDATE memories SET supersedes = ? WHERE id = ?", (old, new))
        conn.commit()
    finally:
        conn.close()
    assert hard_delete_memory(new, actor="ui", db_path=tmp_db)
    left = get_memory(old, db_path=tmp_db)
    assert left.superseded_by is None
    conn = connect(tmp_db)
    try:
        dangling = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE superseded_by = ? OR invalidated_by = ? "
            "OR supersedes = ?", (new, new, new)).fetchone()[0]
        detail = conn.execute("SELECT detail FROM memory_audit WHERE memory_id = ? AND "
                              "action = 'hard_delete'", (new,)).fetchone()[0]
    finally:
        conn.close()
    assert dangling == 0 and old in json.loads(detail)["had_superseded"]
