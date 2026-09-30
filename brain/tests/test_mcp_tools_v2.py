import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.mcp.tools import handle_add_memory, handle_delete_memory, handle_get_startup_summary
from app.storage import DB_PATH


@pytest.mark.asyncio
async def test_handle_add_memory_description_bypasses_llm():
    """When description is provided, entry.summary should be set before ingest (bypassing LLM)."""
    from app.models import MemoryEntry
    mock_result = MemoryEntry(id="xyz", content="long content here", type="note",
                              project="test", summary="My precise description",
                              importance=3, superseded=["old-id"])

    with patch("app.mcp.tools.ingest", new=AsyncMock(return_value=mock_result)) as mock_ingest:
        result = await handle_add_memory(
            content="long content here",
            type="note",
            project="test",
            description="My precise description",
        )
        data = json.loads(result)
        # Verify ingest was called with summary pre-set
        call_entry = mock_ingest.call_args[0][0]
        assert call_entry.summary == "My precise description"
        # Verify response includes superseded
        assert data["superseded"] == ["old-id"]
        assert "potential_supersessions" in data


@pytest.mark.asyncio
async def test_handle_add_memory_no_description_leaves_summary_empty():
    """Without description, entry.summary is left empty so ingest runs LLM summariser."""
    from app.models import MemoryEntry
    mock_result = MemoryEntry(id="abc", content="some content", type="note", project="test",
                              summary="LLM generated summary", importance=4)

    with patch("app.mcp.tools.ingest", new=AsyncMock(return_value=mock_result)) as mock_ingest:
        await handle_add_memory(content="some content", type="note", project="test")
        call_entry = mock_ingest.call_args[0][0]
        assert call_entry.summary == ""  # not pre-set


@pytest.mark.asyncio
async def test_handle_delete_memory_success():
    from app.models import MemoryEntry
    fake_entry = MemoryEntry(content="x", type="note", project="p")
    with patch("app.mcp.tools.get_memory", return_value=fake_entry), \
         patch("app.mcp.tools.delete_memory") as mock_del, \
         patch("app.mcp.tools.vec_delete") as mock_cdel:
        result = await handle_delete_memory(fake_entry.id)
        data = json.loads(result)
        assert data["deleted"] is True
        assert data["id"] == fake_entry.id
        mock_del.assert_called_once()
        mock_cdel.assert_called_once_with(fake_entry.id, db_path=DB_PATH)


@pytest.mark.asyncio
async def test_handle_delete_memory_not_found():
    with patch("app.mcp.tools.get_memory", return_value=None):
        result = await handle_delete_memory("nonexistent")
        data = json.loads(result)
        assert "error" in data


@pytest.mark.asyncio
async def test_handle_get_startup_summary_includes_recent_state():
    from app.models import Project
    from datetime import datetime, timezone
    fake_project = Project(slug="myproj", name="My Project",
                           last_activity=datetime.now(timezone.utc), one_liner="")
    with patch("app.mcp.tools.storage_list_projects", return_value=[fake_project]), \
         patch("app.mcp.tools.get_project_recent_state", return_value="Fixed deploy bug") as mock_state, \
         patch("app.mcp.tools.get_recent", return_value=[]):
        result = await handle_get_startup_summary()
        from unittest.mock import ANY
        mock_state.assert_called_once_with("myproj", db_path=ANY)
        assert "Fixed deploy bug" in result
