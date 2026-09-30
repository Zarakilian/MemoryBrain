"""Implicit choice: reading a search result soon after the search counts as choosing it."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.db import connect
from app.models import MemoryEntry
from app.retrieval import record_retrieval
from app.storage import add_memory


def _chosen(db, event_id):
    conn = connect(db)
    try:
        return conn.execute("SELECT chosen_id FROM retrieval_events WHERE id = ?",
                            (event_id,)).fetchone()[0]
    finally:
        conn.close()


def _mem(db, content="a memory"):
    entry = MemoryEntry(content=content, type="note", project="acme", importance=3)
    add_memory(entry, db_path=db)
    return entry.id


@pytest.fixture
def tools_db(tmp_db, monkeypatch):
    monkeypatch.setattr("app.mcp.tools.DB_PATH", tmp_db)
    return tmp_db


@pytest.mark.asyncio
async def test_reading_a_result_within_ten_minutes_marks_it_chosen(tools_db):
    from app.mcp.tools import handle_get_memory
    mid, other = _mem(tools_db), _mem(tools_db, "another")
    event = record_retrieval("which memory", [other, mid], project="acme", db_path=tools_db)
    await handle_get_memory(mid)
    assert _chosen(tools_db, event["id"]) == mid


@pytest.mark.asyncio
async def test_an_old_search_is_not_credited(tools_db):
    from app.mcp.tools import handle_get_memory
    mid = _mem(tools_db)
    event = record_retrieval("old search", [mid], db_path=tools_db)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    conn = connect(tools_db)
    try:
        with conn:
            conn.execute("UPDATE retrieval_events SET created_at = ? WHERE id = ?",
                         (stale, event["id"]))
    finally:
        conn.close()
    await handle_get_memory(mid)
    assert _chosen(tools_db, event["id"]) is None


@pytest.mark.asyncio
async def test_a_memory_that_was_not_in_the_results_is_not_credited(tools_db):
    from app.mcp.tools import handle_get_memory
    mid, shown = _mem(tools_db), _mem(tools_db, "shown")
    event = record_retrieval("a search", [shown], db_path=tools_db)
    await handle_get_memory(mid)
    assert _chosen(tools_db, event["id"]) is None


@pytest.mark.asyncio
async def test_an_explicit_choice_is_never_overwritten(tools_db):
    from app.mcp.tools import handle_get_memory
    mid, picked = _mem(tools_db), _mem(tools_db, "picked")
    event = record_retrieval("a search", [mid, picked], chosen_id=picked, db_path=tools_db)
    await handle_get_memory(mid)
    assert _chosen(tools_db, event["id"]) == picked
