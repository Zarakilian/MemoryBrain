# tests/conftest.py
import pytest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
import chromadb
from app.storage import init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """In-memory SQLite for tests — no real file I/O."""
    db_path = tmp_path / "test_brain.db"
    monkeypatch.setattr("app.storage.DB_PATH", db_path)
    init_db(db_path)
    return db_path


@pytest.fixture
def mock_ollama():
    """Mock async ollama client so tests don't need a running Ollama instance.

    After the provider-abstraction refactor, the Ollama client lives as
    OllamaProvider._client rather than a module-level ``_client``.  We build a
    real OllamaProvider instance, replace its internal client with the mock,
    inject it as the active provider, and yield the mock client so existing
    tests that assert on ``mock_ollama.generate`` / ``mock_ollama.embeddings``
    continue to work unchanged.
    """
    import app.summarise as s

    mock_client = AsyncMock()
    mock_client.embeddings.return_value = {"embedding": [0.1] * 768}
    mock_client.generate.side_effect = AsyncMock(
        side_effect=lambda model, prompt, **kwargs: {
            "response": "3" if "Rate the importance" in prompt else "Short two sentence summary."
        }
    )

    provider = s.OllamaProvider.__new__(s.OllamaProvider)
    provider._client = mock_client
    provider._embed_model = "embeddinggemma"
    provider._summarise_model = "llama3.2:3b"

    original_provider = s._provider
    s._provider = provider
    try:
        yield mock_client
    finally:
        s._provider = original_provider


@pytest.fixture
def tmp_chroma():
    """In-memory ChromaDB client for tests — no disk writes."""
    client = chromadb.EphemeralClient()
    return client
