# tests/test_search.py
import pytest
from app.search import reciprocal_rank_fusion, hybrid_search
from unittest.mock import patch, AsyncMock


def test_rrf_merges_two_lists_by_rank():
    kw = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    sem = [{"id": "b"}, {"id": "d"}, {"id": "a"}]
    merged = reciprocal_rank_fusion(kw, sem)
    # "b" appears in both at high rank — should score highest
    assert merged[0] == "b"
    assert "a" in merged
    assert "d" in merged


def test_rrf_empty_lists():
    assert reciprocal_rank_fusion([], []) == []


def test_rrf_one_empty_list():
    kw = [{"id": "x"}, {"id": "y"}]
    merged = reciprocal_rank_fusion(kw, [])
    assert merged == ["x", "y"]


@pytest.mark.asyncio
async def test_hybrid_search_returns_summaries_not_content(tmp_db, mock_ollama):
    from app.models import MemoryEntry
    from app.storage import add_memory
    e = MemoryEntry(
        content="grafana clickhouse full content here",
        summary="Grafana dashboard with ClickHouse.",
        type="note",
        project="monitoring",
    )
    add_memory(e, db_path=tmp_db)

    with patch("app.search.DB_PATH", tmp_db), \
         patch("app.search.chroma_search", return_value=[
             {"id": e.id, "metadata": {}, "distance": 0.1}
         ]):
        results = await hybrid_search("grafana clickhouse", limit=5)

    assert len(results) > 0
    assert results[0]["id"] == e.id
    assert "summary" in results[0]
    assert "content" not in results[0]
