"""Tests for MCP call_tool argument validation (H1)."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from app.mcp.tools import call_tool
import mcp.types as types


@pytest.mark.asyncio
async def test_unknown_tool_returns_error():
    result = await call_tool("nonexistent_tool", {})
    assert len(result) == 1
    assert "Unknown tool" in result[0].text


@pytest.mark.asyncio
async def test_search_memory_missing_query_returns_error():
    """search_memory requires 'query' — missing it should return error, not TypeError."""
    result = await call_tool("search_memory", {})
    assert len(result) == 1
    assert "error" in result[0].text.lower() or "missing" in result[0].text.lower()


@pytest.mark.asyncio
async def test_search_memory_limit_clamped_to_100(tmp_db, mock_ollama):
    """limit > 100 should be clamped to 100."""
    with patch("app.mcp.tools.hybrid_search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []
        await call_tool("search_memory", {"query": "test", "limit": 9999})
        _, kwargs = mock_search.call_args
        assert kwargs["limit"] <= 100


@pytest.mark.asyncio
async def test_search_memory_negative_limit_clamped(tmp_db, mock_ollama):
    """Negative limit should be clamped to 1."""
    with patch("app.mcp.tools.hybrid_search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []
        await call_tool("search_memory", {"query": "test", "limit": -5})
        _, kwargs = mock_search.call_args
        assert kwargs["limit"] >= 1


@pytest.mark.asyncio
async def test_get_memory_missing_id_returns_error():
    """get_memory requires 'memory_id' — missing it should return error."""
    result = await call_tool("get_memory", {})
    assert "error" in result[0].text.lower() or "missing" in result[0].text.lower()


@pytest.mark.asyncio
async def test_add_memory_missing_required_returns_error():
    """add_memory requires content, type, project — missing any returns error."""
    result = await call_tool("add_memory", {"content": "hello"})
    assert "error" in result[0].text.lower() or "missing" in result[0].text.lower()


@pytest.mark.asyncio
async def test_extra_keys_ignored_no_crash(tmp_db, mock_ollama):
    """Extra unknown keys in arguments should be stripped, not cause TypeError."""
    with patch("app.mcp.tools.hybrid_search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []
        result = await call_tool("search_memory", {
            "query": "test", "__import__": "os", "evil_key": True
        })
    assert len(result) == 1
    # Should succeed, not crash
    parsed = json.loads(result[0].text)
    assert isinstance(parsed, list)
