# tests/test_mcp_tools.py
import pytest
import json
from unittest.mock import patch, AsyncMock
from app.mcp.tools import (
    handle_search_memory,
    handle_get_memory,
    handle_add_memory,
    handle_get_recent_context,
    handle_list_projects,
    handle_get_startup_summary,
)
from app.models import MemoryEntry, Project


@pytest.mark.asyncio
async def test_search_memory_returns_json_list(tmp_db, mock_ollama):
    with patch("app.mcp.tools.search_with_status", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = ([
            {"id": "abc", "summary": "found item", "project": "api-service", "type": "note",
             "source": "", "importance": 3, "timestamp": "2026-03-27T10:00:00"}
        ], None)
        result = await handle_search_memory(query="grafana", limit=5)
    data = json.loads(result)
    assert isinstance(data, list)
    assert data[0]["id"] == "abc"
    assert "summary" in data[0]


@pytest.mark.asyncio
async def test_get_memory_returns_full_content(tmp_db):
    entry = MemoryEntry(content="full content here", type="note", project="api-service")
    from app.storage import add_memory
    add_memory(entry, db_path=tmp_db)
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_memory(memory_id=entry.id)
    data = json.loads(result)
    assert data["content"] == "full content here"
    assert data["id"] == entry.id


@pytest.mark.asyncio
async def test_get_memory_not_found_returns_error(tmp_db):
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_memory(memory_id="does-not-exist")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_add_memory_calls_ingest_and_returns_id(tmp_db, mock_ollama):
    with patch("app.mcp.tools.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = MemoryEntry(
            id="new-id", content="test", type="note", project="api-service"
        )
        result = await handle_add_memory(
            content="test note", type="note", project="api-service", tags=["grafana"]
        )
    assert "new-id" in result


@pytest.mark.asyncio
async def test_list_projects_returns_project_name(tmp_db):
    p = Project(slug="api-service", name="Api Service Rollout", one_liner="Service rollout")
    from app.storage import upsert_project
    upsert_project(p, db_path=tmp_db)
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_list_projects()
    assert "api-service" in result
    assert "Api Service Rollout" in result


@pytest.mark.asyncio
async def test_get_startup_summary_under_200_tokens(tmp_db):
    from app.storage import upsert_project
    for i in range(5):
        upsert_project(
            Project(slug=f"project-{i}", name=f"Project {i}", one_liner=f"Description {i}"),
            db_path=tmp_db,
        )
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_startup_summary()
    # rough token estimate: 4 chars per token
    assert len(result) / 4 < 200


@pytest.mark.asyncio
async def test_get_startup_summary_includes_recent_memories(tmp_db):
    from app.storage import upsert_project, add_memory
    upsert_project(Project(slug="myproject", name="My Project"), db_path=tmp_db)
    entry = MemoryEntry(
        content="something important happened in api-service",
        summary="important api-service event",
        type="note",
        project="myproject",
    )
    add_memory(entry, db_path=tmp_db)
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_startup_summary()
    assert "Recent Memories" in result
    assert "myproject" in result


@pytest.mark.asyncio
async def test_list_projects_no_plugin_status_header(tmp_db):
    p = Project(slug="api-service", name="Api Service Rollout", one_liner="Service rollout")
    from app.storage import upsert_project
    upsert_project(p, db_path=tmp_db)
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_list_projects()
    assert "## Projects" in result
    assert "Active plugins" not in result
    assert "Inactive plugins" not in result
