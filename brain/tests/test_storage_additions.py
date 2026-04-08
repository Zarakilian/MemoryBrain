# brain/tests/test_storage_additions.py
import pytest
from datetime import datetime
from app.models import MemoryEntry
from app.storage import (
    add_memory, get_memory_by_source,
    get_last_run, set_last_run,
    init_db,
)


def test_get_memory_by_source_returns_entry(tmp_db):
    entry = MemoryEntry(
        content="confluence page about monitoring",
        type="confluence",
        project="eze",
        source="https://confluence.example.com/pages/123",
    )
    add_memory(entry, db_path=tmp_db)
    result = get_memory_by_source("https://confluence.example.com/pages/123", db_path=tmp_db)
    assert result is not None
    assert result.id == entry.id
    assert result.source == "https://confluence.example.com/pages/123"


def test_get_memory_by_source_returns_none_when_missing(tmp_db):
    result = get_memory_by_source("https://not-stored.com/page/999", db_path=tmp_db)
    assert result is None


def test_set_and_get_last_run(tmp_db):
    dt = datetime(2026, 3, 27, 10, 0, 0)
    set_last_run("confluence", dt, db_path=tmp_db)
    result = get_last_run("confluence", db_path=tmp_db)
    assert result == dt


def test_get_last_run_returns_none_when_not_set(tmp_db):
    result = get_last_run("pagerduty", db_path=tmp_db)
    assert result is None


def test_set_last_run_upserts(tmp_db):
    dt1 = datetime(2026, 3, 27, 10, 0, 0)
    dt2 = datetime(2026, 3, 27, 16, 0, 0)
    set_last_run("confluence", dt1, db_path=tmp_db)
    set_last_run("confluence", dt2, db_path=tmp_db)
    result = get_last_run("confluence", db_path=tmp_db)
    assert result.hour == 16
