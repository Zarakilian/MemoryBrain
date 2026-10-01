# tests/test_search.py
import pytest
from app.search import fuse, hybrid_search
from unittest.mock import patch, AsyncMock


def _order(scores):
    return sorted(scores, key=lambda i: scores[i], reverse=True)


def test_fuse_merges_two_lists_by_rank():
    scores = fuse(["a", "b", "c"], ["b", "d", "a"])
    # "b" is near the top of both lists, so it scores highest
    assert _order(scores)[0] == "b"
    assert {"a", "c", "d"} <= set(scores)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)


def test_fuse_empty_lists():
    assert fuse([], []) == {}


def test_fuse_one_empty_list_keeps_its_order():
    assert _order(fuse(["x", "y"], [])) == ["x", "y"]


@pytest.mark.asyncio
async def test_hybrid_search_returns_summaries_not_content(tmp_db, mock_ollama):
    from app.models import MemoryEntry
    from app.storage import add_memory
    e = MemoryEntry(
        content="invoice export full content here",
        summary="Monthly invoice export for finance.",
        type="note",
        project="api-service",
    )
    add_memory(e, db_path=tmp_db)

    with patch("app.search.DB_PATH", tmp_db), \
         patch("app.search.vec_search_multi", return_value=[
             {"id": e.id, "metadata": {}, "distance": 0.1}
         ]):
        results = await hybrid_search("invoice export", limit=5)

    assert len(results) > 0
    assert results[0]["id"] == e.id
    assert "summary" in results[0]
    assert "content" not in results[0]
