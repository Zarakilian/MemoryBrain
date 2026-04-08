# brain/tests/test_confluence_plugin.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime


@pytest.fixture
def mock_confluence_env(monkeypatch):
    monkeypatch.setenv("CONFLUENCE_URL", "https://confluence.example.com")
    monkeypatch.setenv("CONFLUENCE_TOKEN", "test-token-123")


@pytest.fixture
def mock_confluence_http():
    """Mock httpx.AsyncClient for Confluence API calls."""
    with patch("app.ingestion.plugins.confluence.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_client


def _make_page_response(page_id="123", space_key="EZE", title="Test Page",
                         last_modified="2026-03-27T10:00:00.000Z",
                         html_body="<p>Test content</p>"):
    return {
        "results": [{
            "id": page_id,
            "title": title,
            "space": {"key": space_key},
            "version": {"when": last_modified},
            "_links": {"webui": f"/spaces/{space_key}/pages/{page_id}/{title.replace(' ', '+')}"},
            "body": {"storage": {"value": html_body}},
        }],
        "_links": {}
    }


@pytest.mark.asyncio
async def test_health_check_returns_true_when_api_responds(mock_confluence_env, mock_confluence_http):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_confluence_http.get = AsyncMock(return_value=mock_response)

    from app.ingestion.plugins.confluence import health_check
    result = await health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_401(mock_confluence_env, mock_confluence_http):
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_confluence_http.get = AsyncMock(return_value=mock_response)

    from app.ingestion.plugins.confluence import health_check
    result = await health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_false_when_env_missing(monkeypatch):
    monkeypatch.delenv("CONFLUENCE_URL", raising=False)
    monkeypatch.delenv("CONFLUENCE_TOKEN", raising=False)

    from app.ingestion.plugins.confluence import health_check
    result = await health_check()
    assert result is False


@pytest.mark.asyncio
async def test_ingest_returns_memory_entries(mock_confluence_env, mock_confluence_http, tmp_db):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=_make_page_response())
    mock_confluence_http.get = AsyncMock(return_value=mock_response)

    with patch("app.ingestion.plugins.confluence.DB_PATH", tmp_db):
        from app.ingestion.plugins.confluence import ingest
        entries = await ingest(since=datetime(2026, 3, 20))

    assert len(entries) == 1
    assert entries[0].type == "confluence"
    assert entries[0].project == "eze"
    assert "Test content" in entries[0].content
    assert "confluence.example.com" in entries[0].source


@pytest.mark.asyncio
async def test_ingest_strips_html_tags(mock_confluence_env, mock_confluence_http, tmp_db):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=_make_page_response(
        html_body="<h1>Title</h1><p>Body text with <strong>bold</strong></p>"
    ))
    mock_confluence_http.get = AsyncMock(return_value=mock_response)

    with patch("app.ingestion.plugins.confluence.DB_PATH", tmp_db):
        from app.ingestion.plugins.confluence import ingest
        entries = await ingest(since=datetime(2026, 3, 20))

    assert "<h1>" not in entries[0].content
    assert "<p>" not in entries[0].content
    assert "Title" in entries[0].content
    assert "Body text with bold" in entries[0].content


@pytest.mark.asyncio
async def test_ingest_skips_already_stored_page(mock_confluence_env, mock_confluence_http, tmp_db):
    """Deduplication: if source URL exists with same or newer timestamp, skip it."""
    from app.models import MemoryEntry
    from app.storage import add_memory

    # Pre-store this page
    stored = MemoryEntry(
        content="old content",
        type="confluence",
        project="eze",
        source="https://confluence.example.com/spaces/EZE/pages/123/Test+Page",
    )
    add_memory(stored, db_path=tmp_db)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=_make_page_response(
        last_modified="2026-03-25T10:00:00.000Z"  # older than stored entry
    ))
    mock_confluence_http.get = AsyncMock(return_value=mock_response)

    with patch("app.ingestion.plugins.confluence.DB_PATH", tmp_db):
        from app.ingestion.plugins.confluence import ingest
        entries = await ingest(since=datetime(2026, 3, 20))

    assert len(entries) == 0
