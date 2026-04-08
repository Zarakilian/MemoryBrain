# brain/tests/test_pagerduty_plugin.py
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime


@pytest.fixture
def mock_pd_env(monkeypatch):
    monkeypatch.setenv("PAGERDUTY_TOKEN", "test-pd-token")


@pytest.fixture
def mock_pd_http():
    with patch("app.ingestion.plugins.pagerduty.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_client


def _make_incident(
    id="P12345",
    title="Payment gateway timeout",
    urgency="high",
    service="ecosystem-backoffice-framework",
    created="2026-03-27T10:00:00Z",
    resolved="2026-03-27T10:23:00Z",
):
    return {
        "id": id,
        "html_url": f"https://company.pagerduty.com/incidents/{id}",
        "title": title,
        "urgency": urgency,
        "service": {"summary": service},
        "created_at": created,
        "resolved_at": resolved,
    }


@pytest.mark.asyncio
async def test_health_check_returns_true(mock_pd_env, mock_pd_http):
    me_response = MagicMock()
    me_response.status_code = 200
    me_response.json = MagicMock(return_value={"user": {"id": "U123"}})
    mock_pd_http.get = AsyncMock(return_value=me_response)

    from app.ingestion.plugins.pagerduty import health_check
    result = await health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_returns_false_when_token_missing(monkeypatch):
    monkeypatch.delenv("PAGERDUTY_TOKEN", raising=False)

    from app.ingestion.plugins.pagerduty import health_check
    result = await health_check()
    assert result is False


@pytest.mark.asyncio
async def test_ingest_returns_entries(mock_pd_env, mock_pd_http, tmp_db):
    me_response = MagicMock()
    me_response.status_code = 200
    me_response.json = MagicMock(return_value={"user": {"id": "U123"}})

    incidents_response = MagicMock()
    incidents_response.status_code = 200
    incidents_response.json = MagicMock(return_value={
        "incidents": [_make_incident()],
        "more": False,
    })

    mock_pd_http.get = AsyncMock(side_effect=[me_response, incidents_response])

    with patch("app.ingestion.plugins.pagerduty.DB_PATH", tmp_db):
        from app.ingestion.plugins.pagerduty import ingest
        entries = await ingest(since=datetime(2026, 3, 27))

    assert len(entries) == 1
    e = entries[0]
    assert e.type == "pagerduty"
    assert e.project == "pagerduty"
    assert e.importance == 4
    assert "Payment gateway timeout" in e.content
    assert "ecosystem-backoffice-framework" in e.content
    assert "23m" in e.content  # duration in minutes
    assert e.source == "https://company.pagerduty.com/incidents/P12345"


@pytest.mark.asyncio
async def test_ingest_summary_equals_content(mock_pd_env, mock_pd_http, tmp_db):
    """For PD incidents, summary = content (no Ollama needed)."""
    me_response = MagicMock()
    me_response.status_code = 200
    me_response.json = MagicMock(return_value={"user": {"id": "U123"}})

    incidents_response = MagicMock()
    incidents_response.status_code = 200
    incidents_response.json = MagicMock(return_value={
        "incidents": [_make_incident()],
        "more": False,
    })

    mock_pd_http.get = AsyncMock(side_effect=[me_response, incidents_response])

    with patch("app.ingestion.plugins.pagerduty.DB_PATH", tmp_db):
        from app.ingestion.plugins.pagerduty import ingest
        entries = await ingest(since=datetime(2026, 3, 27))

    assert entries[0].summary == entries[0].content


@pytest.mark.asyncio
async def test_ingest_deduplicates_by_source(mock_pd_env, mock_pd_http, tmp_db):
    from app.models import MemoryEntry
    from app.storage import add_memory

    # Pre-store incident
    stored = MemoryEntry(
        content="[high] Payment gateway timeout — ...",
        summary="[high] Payment gateway timeout — ...",
        type="pagerduty",
        project="pagerduty",
        source="https://company.pagerduty.com/incidents/P12345",
        importance=4,
    )
    add_memory(stored, db_path=tmp_db)

    me_response = MagicMock()
    me_response.status_code = 200
    me_response.json = MagicMock(return_value={"user": {"id": "U123"}})

    incidents_response = MagicMock()
    incidents_response.status_code = 200
    incidents_response.json = MagicMock(return_value={
        "incidents": [_make_incident(id="P12345")],
        "more": False,
    })

    mock_pd_http.get = AsyncMock(side_effect=[me_response, incidents_response])

    with patch("app.ingestion.plugins.pagerduty.DB_PATH", tmp_db):
        from app.ingestion.plugins.pagerduty import ingest
        entries = await ingest(since=datetime(2026, 3, 27))

    assert len(entries) == 0
