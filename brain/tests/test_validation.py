"""Tests for input validation (M1-M5)."""
import pytest
from app.models import MemoryEntry, validate_entry, ValidationError

VALID_TYPES = ["session", "handover", "note", "confluence", "pagerduty", "clickhouse", "fact", "file"]


# M1: type enum
def test_valid_types_accepted():
    for t in VALID_TYPES:
        entry = MemoryEntry(content="x", type=t, project="proj")
        validate_entry(entry)  # should not raise


def test_invalid_type_rejected():
    entry = MemoryEntry(content="x", type="evil_type", project="proj")
    with pytest.raises(ValidationError, match="type"):
        validate_entry(entry)


# M2: importance clamp
def test_importance_clamped_low():
    entry = MemoryEntry(content="x", type="note", project="proj", importance=0)
    validate_entry(entry)
    assert entry.importance == 1


def test_importance_clamped_high():
    entry = MemoryEntry(content="x", type="note", project="proj", importance=99)
    validate_entry(entry)
    assert entry.importance == 5


def test_importance_in_range_untouched():
    entry = MemoryEntry(content="x", type="note", project="proj", importance=4)
    validate_entry(entry)
    assert entry.importance == 4


# M3: project slug
def test_valid_project_slug():
    entry = MemoryEntry(content="x", type="note", project="my-proj_123")
    validate_entry(entry)  # should not raise


def test_invalid_project_slug_rejected():
    entry = MemoryEntry(content="x", type="note", project="EVIL <script>")
    with pytest.raises(ValidationError, match="project"):
        validate_entry(entry)


def test_project_slug_too_long():
    entry = MemoryEntry(content="x", type="note", project="a" * 65)
    with pytest.raises(ValidationError, match="project"):
        validate_entry(entry)


# M4: tags bounds
def test_too_many_tags_rejected():
    entry = MemoryEntry(content="x", type="note", project="proj", tags=["t"] * 21)
    with pytest.raises(ValidationError, match="tag"):
        validate_entry(entry)


def test_tag_too_long_rejected():
    entry = MemoryEntry(content="x", type="note", project="proj", tags=["a" * 101])
    with pytest.raises(ValidationError, match="tag"):
        validate_entry(entry)


def test_valid_tags_accepted():
    entry = MemoryEntry(content="x", type="note", project="proj", tags=["foo", "bar"])
    validate_entry(entry)  # should not raise


# M5: content length
def test_content_too_long_rejected():
    entry = MemoryEntry(content="x" * 100_001, type="note", project="proj")
    with pytest.raises(ValidationError, match="content"):
        validate_entry(entry)


def test_empty_content_rejected():
    entry = MemoryEntry(content="", type="note", project="proj")
    with pytest.raises(ValidationError, match="content"):
        validate_entry(entry)
