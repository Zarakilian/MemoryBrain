# tests/test_chroma.py
import pytest
from app.chroma import chroma_add, chroma_search, chroma_delete


def test_add_and_search_returns_matching_id(tmp_chroma):
    embedding = [0.1] * 768
    chroma_add(
        memory_id="abc-123",
        embedding=embedding,
        metadata={"project": "monitoring", "type": "note"},
        client=tmp_chroma,
    )
    results = chroma_search(embedding, n_results=5, client=tmp_chroma)
    ids = [r["id"] for r in results]
    assert "abc-123" in ids


def test_search_with_project_filter(tmp_chroma):
    chroma_add("id-mon", [0.5] * 768, {"project": "monitoring", "type": "note"}, client=tmp_chroma)
    chroma_add("id-other", [0.5] * 768, {"project": "other", "type": "note"}, client=tmp_chroma)
    results = chroma_search(
        [0.5] * 768, n_results=10,
        where={"project": "monitoring"},
        client=tmp_chroma,
    )
    ids = [r["id"] for r in results]
    assert "id-mon" in ids
    assert "id-other" not in ids


def test_search_returns_metadata(tmp_chroma):
    chroma_add("id-1", [0.3] * 768, {"project": "x", "type": "note"}, client=tmp_chroma)
    results = chroma_search([0.3] * 768, client=tmp_chroma)
    assert results[0]["metadata"]["project"] == "x"


def test_chroma_delete(tmp_chroma):
    chroma_add("del-me", [0.2] * 768, {"project": "x", "type": "note"}, client=tmp_chroma)
    chroma_delete("del-me", client=tmp_chroma)
    results = chroma_search([0.2] * 768, client=tmp_chroma)
    ids = [r["id"] for r in results]
    assert "del-me" not in ids
