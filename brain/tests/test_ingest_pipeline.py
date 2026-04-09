# tests/test_ingest_pipeline.py
import pytest
from unittest.mock import AsyncMock, patch
from app.models import MemoryEntry
from app.ingest_pipeline import ingest


@pytest.mark.asyncio
async def test_ingest_stores_entry_in_sqlite(tmp_db, mock_ollama):
    # Content must be > 400 chars to go through Ollama summarisation
    long_content = "clickhouse query is slow because the index is missing on the timestamp column " * 6
    entry = MemoryEntry(content=long_content, type="note", project="monitoring")
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        result = await ingest(entry)
    assert result.summary == "Short two sentence summary."
    assert result.importance == 3

    from app.storage import get_memory
    stored = get_memory(result.id, db_path=tmp_db)
    assert stored is not None
    assert stored.content == long_content


@pytest.mark.asyncio
async def test_ingest_upserts_project(tmp_db, mock_ollama):
    entry = MemoryEntry(content="grafana panel updated", type="note", project="monitoring")
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        await ingest(entry)

    from app.storage import get_project
    project = get_project("monitoring", db_path=tmp_db)
    assert project is not None
    assert project.slug == "monitoring"


@pytest.mark.asyncio
async def test_ingest_calls_chroma_add(tmp_db, mock_ollama):
    entry = MemoryEntry(content="test content", type="note", project="x")
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add") as mock_chroma:
        await ingest(entry)
    mock_chroma.assert_called_once()
    call_args = mock_chroma.call_args
    # First positional arg or keyword arg should be the entry id
    called_id = call_args[1].get("memory_id") or call_args[0][0]
    assert called_id == entry.id
