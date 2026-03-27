# tests/conftest.py
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
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
    """Mock all ollama calls so tests don't need a running Ollama instance."""
    mock_client = MagicMock()
    mock_client.embeddings.return_value = {"embedding": [0.1] * 768}
    mock_client.generate.side_effect = lambda model, prompt, **kwargs: {
        "response": "3" if "Rate the importance" in prompt else "Short two sentence summary."
    }
    with patch("app.summarise._client", mock_client):
        yield mock_client
