# tests/test_models.py
from datetime import datetime
from app.models import MemoryEntry, Project

def test_memory_entry_defaults():
    entry = MemoryEntry(content="test note", type="note", project="api-service")
    assert len(entry.id) == 36          # UUID format
    assert entry.summary == ""
    assert entry.tags == []
    assert entry.importance == 3
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
