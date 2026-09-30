"""v3 embeddings: prompts, model ids, explicit provider, legacy-safe search."""
import pytest

import app.summarise as s
from app.models import MemoryEntry
from app.storage import add_memory
from app.vector import legacy_vector_count, vec_add, vec_search_multi


# ------------------------------------------------------------- provider layer

@pytest.mark.asyncio
async def test_ollama_uses_embed_with_truncate_never_embeddings(mock_ollama):
    vector = await s.embed_document("hello")
    assert len(vector) == 768
    kwargs = mock_ollama.embed.call_args.kwargs
    assert kwargs["truncate"] is True
    assert kwargs["input"] == ["title: none | text: hello"]
    mock_ollama.embeddings.assert_not_called()


@pytest.mark.asyncio
async def test_gemma_gets_document_and_query_prompts(fake_provider):
    await s.embed_document("my car")
    await s.embed_query("automobile")
    assert fake_provider.seen == ["title: none | text: my car",
                                  "task: search result | query: automobile"]
    assert s.embed_model_id() == "fake:embeddinggemma:p1"


@pytest.mark.asyncio
async def test_other_models_get_no_prompt_and_a_raw_id(fake_provider):
    fake_provider._embed_model = "nomic-embed-text"
    await s.embed_document("plain")
    await s.embed_query("plain query")
    assert fake_provider.seen == ["plain", "plain query"]
    assert s.embed_model_id() == "fake:nomic-embed-text:raw"


@pytest.mark.asyncio
async def test_embed_stays_raw_for_legacy_vectors(fake_provider):
    await s.embed("as is")
    assert fake_provider.seen == ["as is"]


@pytest.mark.asyncio
async def test_embed_documents_is_batched_with_prompts(fake_provider):
    vectors = await s.embed_documents(["one car", "two bills"])
    assert len(vectors) == 2
    assert fake_provider.seen == ["title: none | text: one car", "title: none | text: two bills"]


def test_cloud_key_never_switches_the_provider(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "placeholder")
    assert isinstance(s.get_provider(), s.OllamaProvider)
    warning = s.provider_warning()
    assert "GOOGLE_API_KEY" in warning and "MEMORYBRAIN_PROVIDER" in warning


def test_no_warning_without_cloud_keys(monkeypatch):
    for name in ("MEMORYBRAIN_PROVIDER", "GOOGLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert s.provider_warning() is None


def test_explicit_provider_is_honoured(monkeypatch):
    class StubGemini:
        name = "gemini"
    monkeypatch.setattr(s, "GeminiProvider", StubGemini)
    monkeypatch.setenv("MEMORYBRAIN_PROVIDER", "gemini")
    assert isinstance(s.get_provider(), StubGemini)


def test_an_openai_key_never_switches_the_provider(monkeypatch):
    monkeypatch.delenv("MEMORYBRAIN_PROVIDER", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "placeholder")
    assert isinstance(s.get_provider(), s.OllamaProvider)
    assert "OPENAI_API_KEY" in s.provider_warning()


def test_a_named_cloud_provider_without_its_key_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_PROVIDER", "gemini")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        s.get_provider()


@pytest.mark.asyncio
async def test_search_skips_the_raw_query_when_no_legacy_vectors_remain(tmp_db, fake_provider):
    from app.search import hybrid_search
    mid = _mem(tmp_db, "my car is red")
    vec_add(mid, await s.embed_document("my car is red"), {}, db_path=tmp_db,
            model=s.embed_model_id())
    fake_provider.seen.clear()
    await hybrid_search("automobile", db_path=tmp_db)
    assert fake_provider.seen == ["task: search result | query: automobile"]


def test_unknown_provider_is_refused(monkeypatch):
    monkeypatch.setenv("MEMORYBRAIN_PROVIDER", "bogus")
    with pytest.raises(ValueError):
        s.get_provider()


def test_readiness_reports_provider_warning_and_pending(tmp_db, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, patch
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.delenv("MEMORYBRAIN_PROVIDER", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "placeholder")
    monkeypatch.setattr(s, "_provider", None)
    listing = MagicMock()
    listing.models = []
    with patch("app.main.DB_PATH", tmp_db), patch("app.storage.DB_PATH", tmp_db), \
         patch("app.main.ollama_client") as oc, patch("app.main.vec_ready", return_value=True):
        oc.list = AsyncMock(return_value=listing)
        data = TestClient(app).get("/readiness").json()
    assert "GOOGLE_API_KEY" in data["provider_warning"]
    assert data["reembed_pending"] == 0
    assert "ready" in data and "checks" in data


# ------------------------------------------------------------- vectors by model

def _mem(tmp_db, content):
    entry = MemoryEntry(content=content, type="note", project="acme")
    add_memory(entry, db_path=tmp_db)
    return entry.id


def test_vec_add_records_the_model(tmp_db):
    mid = _mem(tmp_db, "x")
    vec_add(mid, [0.5] * 8, {}, db_path=tmp_db, model="fake:embeddinggemma:p1")
    from app.db import connect
    conn = connect(tmp_db)
    try:
        assert conn.execute("SELECT model FROM vec_memories WHERE memory_id = ?",
                            (mid,)).fetchone()[0] == "fake:embeddinggemma:p1"
    finally:
        conn.close()


def test_each_model_is_compared_only_with_its_own_query_vector(tmp_db):
    a = _mem(tmp_db, "a")
    b = _mem(tmp_db, "b")
    vec_add(a, [1.0, 0.0, 0.0, 0.0], {}, db_path=tmp_db, model="A")
    vec_add(b, [0.0, 1.0, 0.0, 0.0], {}, db_path=tmp_db, model="B")
    only_a = vec_search_multi({"A": [1.0, 0.0, 0.0, 0.0]}, db_path=tmp_db)
    assert [r["id"] for r in only_a] == [a] and only_a[0]["model"] == "A"
    both = vec_search_multi({"A": [1.0, 0.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0, 0.0]},
                            db_path=tmp_db)
    assert {r["id"]: r["model"] for r in both} == {a: "A", b: "B"}
    assert all(r["distance"] < 0.01 for r in both)


def test_legacy_vector_count(tmp_db):
    vec_add(_mem(tmp_db, "old"), [0.1] * 8, {}, db_path=tmp_db)
    vec_add(_mem(tmp_db, "new"), [0.1] * 8, {}, db_path=tmp_db, model="A")
    assert legacy_vector_count(db_path=tmp_db) == 1


@pytest.mark.asyncio
async def test_migrated_brain_keeps_semantic_search(tmp_db, fake_provider):
    """A 2.x vector (model '') must still match a query straight after upgrade."""
    from app.search import hybrid_search

    mid = _mem(tmp_db, "my car is red")
    vec_add(mid, await s.embed("my car is red"), {}, db_path=tmp_db)  # legacy, model ''
    results = await hybrid_search("automobile", db_path=tmp_db)
    assert mid in [r["id"] for r in results]
