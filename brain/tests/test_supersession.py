import asyncio
import pytest
from unittest.mock import patch, MagicMock
from app.chroma import chroma_add, chroma_update_metadata
from app.models import MemoryEntry
from app.ingest_pipeline import _check_supersession, SUPERSESSION_THRESHOLDS


def test_chroma_add_includes_status():
    mock_col = MagicMock()
    with patch("app.chroma._get_collection", return_value=mock_col):
        chroma_add("id1", [0.1, 0.2], {"project": "p", "type": "note"})
        call_kwargs = mock_col.upsert.call_args[1]
        assert call_kwargs["metadatas"][0]["status"] == "active"


def test_chroma_update_metadata_archives():
    mock_col = MagicMock()
    with patch("app.chroma._get_collection", return_value=mock_col):
        chroma_update_metadata("id1", {"status": "archived"})
        mock_col.update.assert_called_once_with(ids=["id1"], metadatas=[{"status": "archived"}])


def _entry(type_="note", project="p"):
    return MemoryEntry(content="deploy fix applied to production", type=type_, project=project)


def _make_candidate(distance: float, id_: str = "old-id") -> dict:
    return {"id": id_, "distance": distance, "model": "m",
            "metadata": {"project": "p", "status": "active"}}


async def _check(entry, distance):
    candidates = [_make_candidate(distance)]
    mock_get = MagicMock(return_value=MagicMock(summary="old"))
    with patch("app.ingest_pipeline.vec_search_multi", return_value=candidates) as search, \
         patch("app.ingest_pipeline.get_memory", mock_get):
        result = await _check_supersession(entry, [0.1], "m")
    return result, search


def test_supersession_thresholds_present():
    for t in ["session", "handover", "note", "fact", "decision", "file", "reference", "belief"]:
        assert t in SUPERSESSION_THRESHOLDS
    for t in ["session", "handover", "note", "open_loop", "procedure", "file", "reference"]:
        assert SUPERSESSION_THRESHOLDS[t]["auto"] is None, t
    assert SUPERSESSION_THRESHOLDS["fact"]["auto"] == 0.97
    assert SUPERSESSION_THRESHOLDS["belief"]["auto"] == 0.95


@pytest.mark.asyncio
async def test_search_is_scoped_to_project_type_and_model():
    (_, _), search = await _check(_entry(type_="fact"), 0.5)
    query_vectors = search.call_args.args[0]
    filters = search.call_args.kwargs["filters"]
    assert list(query_vectors) == ["m"]
    assert filters == {"project": "p", "type": "fact", "status": "active"}


@pytest.mark.asyncio
async def test_near_identical_fact_is_superseded():
    (superseded, potential), _ = await _check(_entry(type_="fact"), 0.02)  # similarity 0.98
    assert superseded == ["old-id"] and potential == []


@pytest.mark.asyncio
async def test_similar_fact_is_only_reported():
    (superseded, potential), _ = await _check(_entry(type_="fact"), 0.10)  # similarity 0.90
    assert superseded == []
    assert [p["id"] for p in potential] == ["old-id"]
    assert abs(potential[0]["similarity"] - 0.90) < 0.01


@pytest.mark.asyncio
async def test_a_note_is_never_auto_archived():
    (superseded, potential), _ = await _check(_entry(type_="note"), 0.01)  # similarity 0.99
    assert superseded == []
    assert len(potential) == 1


@pytest.mark.asyncio
async def test_low_similarity_returns_nothing():
    (superseded, potential), _ = await _check(_entry(type_="note"), 0.40)  # similarity 0.60
    assert superseded == [] and potential == []


@pytest.mark.asyncio
async def test_reference_type_never_auto_archives():
    (superseded, potential), _ = await _check(_entry(type_="reference"), 0.01)
    assert superseded == []
    assert len(potential) == 1  # warn still fires (0.99 > 0.80)


@pytest.mark.asyncio
async def test_sessions_are_never_compared():
    (superseded, potential), search = await _check(_entry(type_="session"), 0.01)
    assert superseded == [] and potential == []
    search.assert_not_called()
