import pytest
from unittest.mock import patch, MagicMock
from app.chroma import chroma_add, chroma_update_metadata


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
