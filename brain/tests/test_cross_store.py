"""Tests for A6: cross-store transaction safety between SQLite and ChromaDB."""
import pytest
from unittest.mock import patch, AsyncMock
from app.models import MemoryEntry
from app.ingest_pipeline import ingest


@pytest.mark.asyncio
async def test_sqlite_cleaned_up_on_chroma_failure(tmp_db, mock_ollama):
    """If ChromaDB write fails, the SQLite entry should be rolled back."""
    entry = MemoryEntry(content="orphan test content", type="note", project="test")

    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add", side_effect=RuntimeError("ChromaDB down")):
        with pytest.raises(RuntimeError, match="ChromaDB down"):
            await ingest(entry)

    # SQLite should NOT have the entry
    from app.storage import get_memory
    assert get_memory(entry.id, db_path=tmp_db) is None


@pytest.mark.asyncio
async def test_successful_ingest_stores_both(tmp_db, mock_ollama):
    """Happy path: both stores should have the entry."""
    entry = MemoryEntry(content="both stores test", type="note", project="test")

    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add") as mock_chroma:
        await ingest(entry)

    from app.storage import get_memory
    assert get_memory(entry.id, db_path=tmp_db) is not None
    mock_chroma.assert_called_once()
