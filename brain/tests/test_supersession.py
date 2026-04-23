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
    return {"id": id_, "distance": distance, "metadata": {"project": "p", "status": "active"}}


def test_supersession_thresholds_present():
    for t in ["session", "handover", "note", "fact", "file", "reference"]:
        assert t in SUPERSESSION_THRESHOLDS
    assert SUPERSESSION_THRESHOLDS["reference"]["auto"] is None


@pytest.mark.asyncio
async def test_high_similarity_returns_superseded():
    entry = _entry(type_="note")
    # distance 0.05 → similarity 0.95 → above note auto threshold 0.90
    candidates = [_make_candidate(0.05)]
    mock_get = MagicMock(return_value=MagicMock(summary="old note"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = await _check_supersession(entry, [0.1])
    assert "old-id" in superseded
    assert potential == []


@pytest.mark.asyncio
async def test_medium_similarity_returns_potential():
    entry = _entry(type_="note")
    # distance 0.15 → similarity 0.85 → above warn (0.75) but below auto (0.90)
    candidates = [_make_candidate(0.15)]
    mock_get = MagicMock(return_value=MagicMock(summary="old note"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = await _check_supersession(entry, [0.1])
    assert superseded == []
    assert len(potential) == 1
    assert potential[0]["id"] == "old-id"
    assert abs(potential[0]["similarity"] - 0.85) < 0.01


@pytest.mark.asyncio
async def test_low_similarity_returns_nothing():
    entry = _entry(type_="note")
    # distance 0.40 → similarity 0.60 → below warn (0.75)
    candidates = [_make_candidate(0.40)]
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", MagicMock(return_value=None)):
        superseded, potential = await _check_supersession(entry, [0.1])
    assert superseded == []
    assert potential == []


@pytest.mark.asyncio
async def test_reference_type_never_auto_archives():
    entry = _entry(type_="reference")
    # distance 0.01 → similarity 0.99 — very high, but reference never auto-archives
    candidates = [_make_candidate(0.01)]
    mock_get = MagicMock(return_value=MagicMock(summary="ref"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = await _check_supersession(entry, [0.1])
    assert superseded == []  # reference: auto is None — never archived
    assert len(potential) == 1  # but warn still fires (0.99 > 0.80)


@pytest.mark.asyncio
async def test_session_type_lower_threshold():
    entry = _entry(type_="session")
    # distance 0.15 → similarity 0.85 → above session auto threshold 0.80
    candidates = [_make_candidate(0.15)]
    mock_get = MagicMock(return_value=MagicMock(summary="old session"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = await _check_supersession(entry, [0.1])
    assert "old-id" in superseded
