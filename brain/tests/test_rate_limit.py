"""Tests for L6: concurrency limiting on ingest pipeline."""
import asyncio
import pytest
from unittest.mock import patch, AsyncMock
from app.models import MemoryEntry
from app.ingest_pipeline import ingest, MAX_CONCURRENT_INGESTS


def test_max_concurrent_ingests_is_sane():
    """The semaphore limit should be a reasonable small number."""
    assert 1 <= MAX_CONCURRENT_INGESTS <= 10


@pytest.mark.asyncio
async def test_concurrent_ingests_are_limited(tmp_db, mock_ollama):
    """When more than MAX_CONCURRENT_INGESTS run concurrently, excess should queue."""
    # Track how many are running simultaneously
    running = 0
    max_running = 0
    lock = asyncio.Lock()

    original_embed = mock_ollama.embeddings

    async def slow_embed(*args, **kwargs):
        nonlocal running, max_running
        async with lock:
            running += 1
            max_running = max(max_running, running)
        await asyncio.sleep(0.05)  # simulate slow operation
        async with lock:
            running -= 1
        return {"embedding": [0.1] * 768}

    mock_ollama.embeddings.side_effect = slow_embed

    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        tasks = [
            ingest(MemoryEntry(content=f"entry {i}", type="note", project="test"))
            for i in range(MAX_CONCURRENT_INGESTS + 2)
        ]
        await asyncio.gather(*tasks)

    assert max_running <= MAX_CONCURRENT_INGESTS
