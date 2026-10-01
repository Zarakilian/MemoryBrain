"""v3 write path: never lose text, chunk vectors, safe supersession, write report."""
import json
import math

import pytest

import app.summarise as s
from app.db import connect
from app.ingest_pipeline import ingest, write_report
from app.models import MemoryEntry, ValidationError
from app.storage import add_memory, get_memory
from app.vector import vec_add

REPORT_KEYS = {"id", "summary", "importance", "type", "tags", "trust", "writer", "embedded",
               "chunks", "content_chars", "superseded", "potential_supersessions",
               "warnings", "duplicate"}


@pytest.fixture
def ing_db(tmp_db, monkeypatch):
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    return tmp_db


def _long_session(chars: int) -> str:
    parts, n = [], 0
    while sum(len(p) for p in parts) < chars:
        parts.append(f"## Step {n}\n\nWe checked the car and the invoice again. "
                     f"Result {n} was fine.\n\n" + "Detail line with words. " * 20 + "\n\n")
        n += 1
    return "".join(parts)[:chars]


def _row(db, memory_id, cols):
    conn = connect(db)
    try:
        return conn.execute(f"SELECT {cols} FROM memories WHERE id = ?", (memory_id,)).fetchone()
    finally:
        conn.close()


def _unit(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _vector_at_cosine(v, cos):
    """A vector whose cosine with v is exactly `cos`."""
    a = _unit(v)
    w = [0.0] * len(a)
    w[-1] = 1.0
    dot = sum(x * y for x, y in zip(w, a))
    w = _unit([x - dot * y for x, y in zip(w, a)])  # orthogonal to a
    k = math.sqrt(1 - cos * cos)
    return [cos * x + k * y for x, y in zip(a, w)]


async def _old_fact(db, content, vector):
    entry = MemoryEntry(content=content, type="fact", project="acme", importance=4)
    add_memory(entry, db_path=db)
    vec_add(entry.id, vector, {}, db_path=db, model=s.embed_model_id())
    return entry.id


# ------------------------------------------------------------- never lose text

@pytest.mark.asyncio
async def test_long_session_is_stored_whole_with_chunk_vectors(ing_db, fake_provider):
    body = _long_session(60_000)
    result = await ingest(MemoryEntry(content=body, type="session", project="acme"))
    report = write_report(result)
    assert report["embedded"] is True and report["chunks"] > 0
    assert report["content_chars"] == 60_000
    assert get_memory(result.id, db_path=ing_db).content == body
    conn = connect(ing_db)
    try:
        spans = conn.execute("SELECT start_char, end_char, model FROM vec_chunks "
                             "WHERE memory_id = ? ORDER BY chunk_ix", (result.id,)).fetchall()
    finally:
        conn.close()
    assert len(spans) == report["chunks"]
    assert spans[0]["start_char"] == 0 and spans[-1]["end_char"] == 60_000
    assert all(r["model"] == s.embed_model_id() for r in spans)
    assert all(b["start_char"] < a["end_char"] for a, b in zip(spans, spans[1:]))  # overlap


@pytest.mark.asyncio
async def test_embedding_failure_still_stores_the_row(ing_db, fake_provider):
    fake_provider.fail_on = {"title: none"}  # every document embed fails
    result = await ingest(MemoryEntry(content="the car broke down", type="note", project="acme"))
    report = write_report(result)
    assert report["embedded"] is False
    assert "embedding failed: RuntimeError" in report["warnings"]
    assert get_memory(result.id, db_path=ing_db).content == "the car broke down"
    assert _row(ing_db, result.id, "embedded")[0] == 0


@pytest.mark.asyncio
async def test_short_body_is_its_own_summary(ing_db, fake_provider):
    result = await ingest(MemoryEntry(content="short and sweet", type="note", project="acme"))
    assert result.summary == "short and sweet"


@pytest.mark.asyncio
async def test_summariser_failure_falls_back_to_the_opening(ing_db, fake_provider):
    async def broken(content, max_sentences=3):
        raise RuntimeError("model gone")
    fake_provider.summarise = broken
    body = "word " * 200
    result = await ingest(MemoryEntry(content=body, type="note", project="acme"))
    assert result.summary == body[:280]
    assert "summary fallback" in write_report(result)["warnings"]


def test_summary_input_keeps_the_head_and_tail():
    body = "H" * 5000 + "T" * 2000
    text = s.summary_input(body)
    assert text == "H" * 3000 + "\n…\n" + "T" * 1000
    assert s.summary_input("short") == "short"


# ------------------------------------------------------------- importance

@pytest.mark.asyncio
async def test_importance_defaults(ing_db, fake_provider):
    fact = await ingest(MemoryEntry(content="The car is blue.", type="fact", project="acme"))
    loop = await ingest(MemoryEntry(content="Fix the invoice export", type="open_loop",
                                    project="acme"))
    note = await ingest(MemoryEntry(content="a plain note", type="note", project="acme"))
    kept = await ingest(MemoryEntry(content="explicit three", type="note", project="acme",
                                    importance=3))
    assert (fact.importance, loop.importance, note.importance, kept.importance) == (4, 3, 3, 3)


def test_add_memory_writes_three_when_importance_is_missing(tmp_db):
    entry = MemoryEntry(content="raw write", type="note", project="acme")
    assert entry.importance is None
    add_memory(entry, db_path=tmp_db)
    assert get_memory(entry.id, db_path=tmp_db).importance == 3


# ------------------------------------------------------------- limits

@pytest.mark.asyncio
async def test_long_facts_and_open_loops_are_still_rejected(ing_db, fake_provider):
    with pytest.raises(ValidationError):
        await ingest(MemoryEntry(content="x" * 4001, type="fact", project="acme"))
    with pytest.raises(ValidationError):
        await ingest(MemoryEntry(content="x" * 801, type="open_loop", project="acme"))


def test_rest_validation_error_is_a_422(ing_db, fake_provider, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    resp = TestClient(app).post("/ingest/note", json={"content": "x", "project": "Not A Slug!"})
    assert resp.status_code == 422
    assert "project" in resp.text


# ------------------------------------------------------------- supersession

@pytest.mark.asyncio
async def test_a_new_session_never_archives_an_earlier_one(ing_db, fake_provider):
    first = await ingest(MemoryEntry(content="Session: fixed the car.", type="session",
                                     project="acme"))
    second = await ingest(MemoryEntry(content="Session: fixed the car!", type="session",
                                      project="acme"))
    assert second.superseded == []
    assert get_memory(first.id, db_path=ing_db).status == "active"


@pytest.mark.asyncio
async def test_near_identical_fact_closes_the_old_one(ing_db, fake_provider):
    new_vector = await s.embed_document("The car is red.")
    old = await _old_fact(ing_db, "The car is green.", _vector_at_cosine(new_vector, 0.98))
    result = await ingest(MemoryEntry(content="The car is red.", type="fact", project="acme"))
    assert result.superseded == [old]
    status, superseded_by, invalidated_by, valid_to = _row(
        ing_db, old, "status, superseded_by, invalidated_by, valid_to")
    assert (status, superseded_by, invalidated_by) == ("archived", result.id, result.id)
    assert valid_to is not None
    assert _row(ing_db, result.id, "valid_from")[0] == valid_to


@pytest.mark.asyncio
async def test_similar_fact_below_the_bar_is_only_reported(ing_db, fake_provider):
    new_vector = await s.embed_document("The car is red.")
    old = await _old_fact(ing_db, "The car was red once.", _vector_at_cosine(new_vector, 0.9))
    result = await ingest(MemoryEntry(content="The car is red.", type="fact", project="acme"))
    assert result.superseded == []
    assert [p["id"] for p in result.potential_supersessions] == [old]
    assert get_memory(old, db_path=ing_db).status == "active"


# ------------------------------------------------------------- redaction, dedup, report

@pytest.mark.asyncio
async def test_secrets_are_redacted_before_storage(ing_db, fake_provider):
    token = "ghp_" + "A1b2" * 9
    result = await ingest(MemoryEntry(content=f"use {token} for the push", type="note",
                                      project="acme"))
    stored = get_memory(result.id, db_path=ing_db).content
    assert token not in stored and "[REDACTED:github-token]" in stored
    assert "redacted: github-token" in write_report(result)["warnings"]


@pytest.mark.asyncio
async def test_mcp_add_memory_twice_is_a_duplicate(ing_db, fake_provider):
    from app.mcp.tools import handle_add_memory

    first = json.loads(await handle_add_memory("same words", "note", "acme"))
    second = json.loads(await handle_add_memory("same words", "note", "acme"))
    assert set(first) == REPORT_KEYS
    assert first["duplicate"] is False and second["duplicate"] is True
    assert second["id"] == first["id"]
    assert (first["writer"], first["trust"]) == ("unknown", "agent")  # no transport, no source


@pytest.mark.asyncio
async def test_mcp_writer_comes_from_source_and_trust_is_never_user(ing_db, fake_provider):
    from app.mcp.tools import handle_add_memory

    report = json.loads(await handle_add_memory("from grok", "note", "acme", source=" Grok "))
    assert (report["writer"], report["trust"]) == ("grok", "agent")


def test_old_pre_compact_hook_request_still_works(ing_db, fake_provider, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    body = {"content": "Session transcript tail: the car is fixed.", "project": "acme",
            "source": "pre-compact:2026-09-30T10:00:00+00:00"}
    first = TestClient(app).post("/ingest/session", json=body)
    assert first.status_code == 201
    assert set(first.json()) == REPORT_KEYS
    assert first.json()["writer"] == "hook"
    again = TestClient(app).post("/ingest/session", json=body)
    assert again.status_code == 200 and again.json()["duplicate"] is True


@pytest.mark.asyncio
async def test_a_session_stored_while_embedding_is_down_still_chains(ing_db, fake_provider):
    first = await ingest(MemoryEntry(content="Session one: the car.", type="session",
                                     project="acme"))
    fake_provider.fail_on = {"title: none"}
    second = await ingest(MemoryEntry(content="Session two: the invoice.", type="session",
                                      project="acme"))
    assert second.embedded is False
    conn = connect(ing_db)
    try:
        chained = conn.execute(
            "SELECT COUNT(*) FROM memory_links WHERE kind = 'session_chain' "
            "AND ((src_id = ? AND dst_id = ?) OR (src_id = ? AND dst_id = ?))",
            (second.id, first.id, first.id, second.id)).fetchone()[0]
    finally:
        conn.close()
    assert chained == 1


@pytest.mark.asyncio
async def test_a_provider_that_cannot_start_never_loses_the_text(ing_db, monkeypatch):
    import app.summarise as s

    def broken():
        raise ValueError("MEMORYBRAIN_PROVIDER must be one of ollama, gemini, openai")
    monkeypatch.setattr(s, "_provider", None)
    monkeypatch.setattr(s, "get_provider", broken)
    result = await ingest(MemoryEntry(content="kept anyway", type="note", project="acme"))
    report = write_report(result)
    assert report["embedded"] is False and get_memory(result.id, db_path=ing_db) is not None


@pytest.mark.asyncio
async def test_a_duplicate_written_mid_flight_is_caught_before_storing(ing_db, fake_provider,
                                                                       monkeypatch):
    import app.ingest_pipeline as ip
    real = ip._ensure_summary

    async def racing(entry, warnings):
        add_memory(MemoryEntry(content=entry.content, type=entry.type, project=entry.project,
                               importance=3), db_path=ing_db)  # another call won the race
        await real(entry, warnings)
    monkeypatch.setattr(ip, "_ensure_summary", racing)
    result = await ingest(MemoryEntry(content="sent twice", type="note", project="acme"))
    assert result.duplicate is True
    conn = connect(ing_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories WHERE content = 'sent twice'"
                            ).fetchone()[0] == 1
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_a_linker_failure_does_not_fail_the_write(ing_db, fake_provider, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("linker down")
    monkeypatch.setattr("app.linker.link_new_memory", boom)
    result = await ingest(MemoryEntry(content="still stored", type="note", project="acme"))
    assert get_memory(result.id, db_path=ing_db) is not None
