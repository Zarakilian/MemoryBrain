# tests/test_models.py
from datetime import datetime
from app.models import MemoryEntry, Project

def test_memory_entry_defaults():
    entry = MemoryEntry(content="test note", type="note", project="api-service")
    assert len(entry.id) == 36          # UUID format
    assert entry.summary == ""
    assert entry.tags == []
    assert entry.importance is None  # v3: None means "score it at ingest"
    assert entry.source == ""
    assert isinstance(entry.timestamp, datetime)

def test_memory_entry_custom_fields():
    entry = MemoryEntry(
        content="important thing",
        type="reference",
        project="api-service",
        tags=["alerting", "dashboards"],
        importance=5,
        source="https://wiki.example.com/page/123",
    )
    assert entry.tags == ["alerting", "dashboards"]
    assert entry.importance == 5

def test_project_defaults():
    p = Project(slug="api-service", name="Api Service Rollout")
    assert p.one_liner == ""
    assert isinstance(p.last_activity, datetime)

def test_memory_entry_valid_types():
    valid = ["session", "handover", "note", "reference", "fact", "file", "decision", "open_loop"]
    for t in valid:
        entry = MemoryEntry(content="x", type=t, project="p")
        assert entry.type == t


# ------------------------------------------------------------- v3 fields

import pytest

from app.models import VALID_STATUSES, VALID_TRUST, VALID_TYPES, ValidationError, validate_entry


def test_v3_defaults():
    e = MemoryEntry(content="x", type="note", project="acme")
    assert (e.writer, e.trust, e.embedded) == ("", "agent", True)
    assert (e.valid_from, e.valid_to, e.invalidated_by) == (None, None, None)


def test_procedure_type_and_v3_vocabularies():
    assert "procedure" in VALID_TYPES
    assert VALID_TRUST == {"user", "agent", "derived", "imported"}
    assert VALID_STATUSES == {"active", "archived", "proposed", "done"}


def test_validate_entry_rejects_unknown_trust():
    with pytest.raises(ValidationError):
        validate_entry(MemoryEntry(content="x", type="note", project="acme", trust="root"))


def test_validate_entry_rejects_unknown_status():
    with pytest.raises(ValidationError):
        validate_entry(MemoryEntry(content="x", type="note", project="acme", status="zombie"))


def test_v3_fields_round_trip_through_storage(tmp_db):
    from app.storage import add_memory, get_memory

    e = MemoryEntry(content="fact body", type="fact", project="acme", writer="claude@WORK-PC",
                    trust="user", embedded=False, valid_from="2026-01-01T00:00:00+00:00",
                    valid_to="2026-06-01T00:00:00+00:00", invalidated_by="later-fact-id")
    add_memory(e, db_path=tmp_db)
    got = get_memory(e.id, db_path=tmp_db)
    assert (got.writer, got.trust, got.embedded) == ("claude@WORK-PC", "user", False)
    assert got.valid_from == "2026-01-01T00:00:00+00:00"
    assert got.valid_to == "2026-06-01T00:00:00+00:00"
    assert got.invalidated_by == "later-fact-id"
