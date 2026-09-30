"""v3: the memory row and its vectors commit in one transaction."""
import pytest
from unittest.mock import patch
from app.db import connect
from app.models import MemoryEntry
from app.ingest_pipeline import ingest


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_vector_write_fails(tmp_db, mock_ollama):
    """If storing the vectors fails, the row is rolled back and the error propagates."""
    entry = MemoryEntry(content="orphan test content", type="note", project="test")

    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.insert_vectors", side_effect=RuntimeError("vector write failed")):
        with pytest.raises(RuntimeError, match="vector write failed"):
            await ingest(entry)

    from app.storage import get_memory
    assert get_memory(entry.id, db_path=tmp_db) is None


@pytest.mark.asyncio
async def test_successful_ingest_stores_row_and_vector(tmp_db, mock_ollama):
    entry = MemoryEntry(content="both stores test", type="note", project="test")

    with patch("app.ingest_pipeline.DB_PATH", tmp_db):
        await ingest(entry)

    from app.storage import get_memory
    assert get_memory(entry.id, db_path=tmp_db) is not None
    conn = connect(tmp_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM vec_memories WHERE memory_id = ?",
                            (entry.id,)).fetchone()[0] == 1
    finally:
        conn.close()
