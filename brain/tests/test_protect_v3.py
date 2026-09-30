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
