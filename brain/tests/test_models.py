# tests/test_models.py
from datetime import datetime
from app.models import MemoryEntry, Project

def test_memory_entry_defaults():
    entry = MemoryEntry(content="test note", type="note", project="monitoring")
    assert len(entry.id) == 36          # UUID format
    assert entry.summary == ""
    assert entry.tags == []
    assert entry.importance == 3
    assert entry.source == ""
    assert isinstance(entry.timestamp, datetime)

def test_memory_entry_custom_fields():
    entry = MemoryEntry(
        content="important thing",
        type="confluence",
        project="monitoring",
        tags=["alerting", "grafana"],
        importance=5,
        source="https://confluence.example.com/page/123",
    )
    assert entry.tags == ["alerting", "grafana"]
    assert entry.importance == 5

def test_project_defaults():
    p = Project(slug="monitoring", name="Monitoring Migration")
    assert p.one_liner == ""
    assert isinstance(p.last_activity, datetime)

def test_memory_entry_valid_types():
    valid = ["session", "handover", "note", "confluence", "pagerduty", "clickhouse", "fact", "file"]
    for t in valid:
        entry = MemoryEntry(content="x", type=t, project="p")
        assert entry.type == t
