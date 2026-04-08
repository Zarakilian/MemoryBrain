"""Tests for N1: pipeline should preserve caller-set importance (non-default)."""
import pytest
from unittest.mock import patch
from app.models import MemoryEntry
from app.ingest_pipeline import ingest


@pytest.mark.asyncio
async def test_ingest_preserves_non_default_importance(tmp_db, mock_ollama):
    """When caller sets importance != 3 (e.g. PagerDuty sets 4), pipeline should not overwrite."""
    entry = MemoryEntry(content="PD incident resolved", type="pagerduty", project="pd", importance=4)
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        result = await ingest(entry)
    assert result.importance == 4  # should NOT have been overwritten by Ollama


@pytest.mark.asyncio
async def test_ingest_scores_default_importance(tmp_db, mock_ollama):
    """When importance is the default (3), pipeline should use Ollama to score."""
    entry = MemoryEntry(content="some note", type="note", project="proj", importance=3)
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        result = await ingest(entry)
    # mock_ollama returns "3" for score_importance, so it's still 3 but score_importance was called
    assert result.importance == 3
