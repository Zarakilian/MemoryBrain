# tests/conftest.py
import pytest
from pathlib import Path
from app.storage import init_db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """In-memory SQLite for tests — no real file I/O."""
    db_path = tmp_path / "test_brain.db"
    monkeypatch.setattr("app.storage.DB_PATH", db_path)
    init_db(db_path)
    return db_path
