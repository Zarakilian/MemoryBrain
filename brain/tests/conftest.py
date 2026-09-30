# tests/conftest.py
import os
import re

# FastAPI's TestClient sends Host: testserver; the v3 Host check only answers
# loopback names unless a host is listed here. Host-check tests set their own.
os.environ.setdefault("MEMORYBRAIN_ALLOWED_HOSTS", "testserver")

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

    def _embed(model, input, **kwargs):
        texts = input if isinstance(input, list) else [input]
        return {"embeddings": [[0.1] * 768 for _ in texts]}

    mock_client.embed.side_effect = _embed
    def _generate(model, prompt, **kwargs):
        if "Rate the importance" in prompt:
            return {"response": "3"}
        sources = prompt.split("Sources:", 1)[1] if "Sources:" in prompt else ""
        tags = re.findall(r"\[m:[^\]\s]+\]", sources)
        if tags:  # a v3 belief prompt: cite the first real source
            return {"response": f"Short cited summary {tags[0]}."}
        return {"response": "Short two sentence summary."}

    mock_client.generate.side_effect = AsyncMock(side_effect=_generate)

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


class FakeEmbedProvider:
    """Deterministic, model-free embeddings for tests.

    One dimension per concept and synonyms share a dimension, so "automobile"
    finds "my car" semantically while keyword search cannot. Every text the
    provider sees is kept in `seen` (prompt prefixes included); any text
    containing a string in `fail_on` raises.
    """
    CONCEPTS = {"car": 0, "automobile": 0, "vehicle": 0, "invoice": 1, "bill": 1,
                "weather": 2, "rain": 2, "deploy": 3, "release": 3}
    name = "fake"

    def __init__(self, embed_model: str = "embeddinggemma"):
        self._embed_model = embed_model
        self._summarise_model = "fake-summary"
        self.seen: list[str] = []
        self.fail_on: set[str] = set()

    async def embed(self, text: str) -> list[float]:
        import re
        self.seen.append(text)
        if any(bad in text for bad in self.fail_on):
            raise RuntimeError("fake embed failure")
        vector = [0.01] * 8
        for word in re.findall(r"[a-z]+", text.lower()):
            if word in self.CONCEPTS:
                vector[self.CONCEPTS[word]] += 1.0
        return vector

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        return content[:100]

    async def score_importance(self, content: str) -> int:
        return 3


@pytest.fixture
def fake_provider(monkeypatch):
    """Install FakeEmbedProvider as the active provider for one test."""
    import app.summarise as s
    provider = FakeEmbedProvider()
    monkeypatch.setattr(s, "_provider", provider)
    return provider
