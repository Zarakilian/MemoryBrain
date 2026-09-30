"""Background re-embed of legacy or missing vectors."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import app.reembed as reembed
import app.summarise as s
from app.db import connect
from app.models import MemoryEntry
from app.storage import add_memory
from app.vector import vec_add


def _mem(tmp_db, content, age_days=0, legacy_vector=True):
    entry = MemoryEntry(content=content, type="note", project="acme",
                        timestamp=datetime.now(timezone.utc) - timedelta(days=age_days))
    add_memory(entry, db_path=tmp_db)
    if legacy_vector:
        vec_add(entry.id, [0.2] * 8, {}, db_path=tmp_db)
    return entry.id


def _rows(tmp_db):
    conn = connect(tmp_db)
    try:
        return {r["id"]: (r["model"], r["embedded"]) for r in conn.execute(
            "SELECT m.id, v.model, m.embedded FROM memories m "
            "LEFT JOIN vec_memories v ON v.memory_id = m.id")}
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _fresh_failures():
    reembed._recent_failures.clear()
    yield
    reembed._recent_failures.clear()


@pytest.mark.asyncio
async def test_reembed_upgrades_legacy_vectors(tmp_db, fake_provider):
    ids = [_mem(tmp_db, f"note {i}") for i in range(3)]
    assert reembed.pending_count(db_path=tmp_db) == 3
    report = await reembed.reembed_batch(10, db_path=tmp_db)
    assert report == {"done": 3, "failed": 0, "pending": 0}
    rows = _rows(tmp_db)
    assert all(rows[i] == (s.embed_model_id(), 1) for i in ids)


def test_memory_without_a_vector_is_pending(tmp_db, fake_provider):
    _mem(tmp_db, "never embedded", legacy_vector=False)
    assert reembed.pending_count(db_path=tmp_db) == 1


@pytest.mark.asyncio
async def test_a_failing_row_is_counted_and_the_batch_continues(tmp_db, fake_provider, monkeypatch):
    good = [_mem(tmp_db, "alpha"), _mem(tmp_db, "gamma")]
    bad = _mem(tmp_db, "poison pill")
    fake_provider.fail_on = {"poison"}
    report = await reembed.reembed_batch(10, db_path=tmp_db)
    assert report == {"done": 2, "failed": 1, "pending": 1}
    rows = _rows(tmp_db)
    assert rows[bad][1] == 0 and all(rows[g][1] == 1 for g in good)
    fake_provider.fail_on.clear()
    monkeypatch.setattr(reembed, "RETRY_AFTER_S", 0)
    assert (await reembed.reembed_batch(10, db_path=tmp_db))["pending"] == 0
    assert reembed.pending_count(db_path=tmp_db) == 0


@pytest.mark.asyncio
async def test_a_recent_failure_does_not_block_the_queue(tmp_db, fake_provider):
    bad = _mem(tmp_db, "poison old", age_days=3)
    newer = _mem(tmp_db, "fine newer", age_days=1)
    fake_provider.fail_on = {"poison"}
    await reembed.reembed_batch(1, db_path=tmp_db)  # oldest first: the bad one
    await reembed.reembed_batch(1, db_path=tmp_db)  # skips the recent failure
    rows = _rows(tmp_db)
    assert rows[newer] == (s.embed_model_id(), 1)
    assert rows[bad][1] == 0


@pytest.mark.asyncio
async def test_oldest_first_within_the_limit(tmp_db, fake_provider):
    old = _mem(tmp_db, "old one", age_days=5)
    new = _mem(tmp_db, "new one", age_days=0)
    await reembed.reembed_batch(1, db_path=tmp_db)
    rows = _rows(tmp_db)
    assert rows[old][0] == s.embed_model_id() and rows[new][0] == ""


@pytest.mark.asyncio
async def test_loop_works_through_the_queue_and_stops(tmp_db, fake_provider, monkeypatch):
    monkeypatch.setattr(reembed, "START_DELAY_S", 0)
    monkeypatch.setattr(reembed, "TICK_S", 0.01)
    for i in range(3):
        _mem(tmp_db, f"loop {i}")
    stop = asyncio.Event()
    task = asyncio.create_task(reembed.reembed_loop(stop, rate_per_min=2, db_path=tmp_db))
    for _ in range(200):
        if reembed.pending_count(db_path=tmp_db) == 0:
            break
        await asyncio.sleep(0.01)
    stop.set()
    await asyncio.wait_for(task, timeout=2)
    assert reembed.pending_count(db_path=tmp_db) == 0


def test_backfill_endpoint_runs_a_500_batch(monkeypatch):
    from unittest.mock import AsyncMock, patch
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    batch = AsyncMock(return_value={"done": 0, "failed": 0, "pending": 0})
    with patch("app.main.reembed_batch", batch), \
         patch("app.main.startup_backfill", return_value={"skipped": True}):
        resp = TestClient(app, headers={"X-Brain-Client": "test"}).post("/admin/backfill-vectors")
    assert resp.status_code == 200
    batch.assert_awaited_once()
    assert batch.await_args.args[0] == 500


def test_status_reports_reembed_pending(tmp_db, monkeypatch, fake_provider):
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    _mem(tmp_db, "waiting")
    with patch("app.main.DB_PATH", tmp_db), patch("app.storage.DB_PATH", tmp_db):
        data = TestClient(app).get("/status").json()
    assert data["reembed_pending"] == 1
