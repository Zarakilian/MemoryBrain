"""Tests for the ClickHouse APM plugin."""
import pytest
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import json


@pytest.fixture(autouse=True)
def ch_env(monkeypatch):
    monkeypatch.setenv("CLICKHOUSE_IOM_URL", "https://clickhouse.example.com/query")
    monkeypatch.setenv("CLICKHOUSE_TOKEN", "test-token")


def _make_ch_response(rows: list[dict]) -> MagicMock:
    """Build a mock httpx response that returns ClickHouse JSON-Each format rows."""
    mock = MagicMock()
    mock.status_code = 200
    # ClickHouse JSONEachRow: one JSON object per line
    mock.text = "\n".join(json.dumps(r) for r in rows)
    return mock


# ── health_check ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_returns_true_on_200():
    from app.ingestion.plugins.clickhouse import health_check
    with patch("app.ingestion.plugins.clickhouse.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = MagicMock(status_code=200, text="1\n")
        result = await health_check()
    assert result is True


@pytest.mark.asyncio
async def test_health_check_returns_false_on_error():
    from app.ingestion.plugins.clickhouse import health_check
    with patch("app.ingestion.plugins.clickhouse.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = MagicMock(status_code=401, text="")
        result = await health_check()
    assert result is False


@pytest.mark.asyncio
async def test_health_check_returns_false_when_env_missing(monkeypatch):
    monkeypatch.delenv("CLICKHOUSE_IOM_URL", raising=False)
    monkeypatch.delenv("CLICKHOUSE_TOKEN", raising=False)
    from app.ingestion.plugins import clickhouse
    import importlib
    importlib.reload(clickhouse)
    from app.ingestion.plugins.clickhouse import health_check
    result = await health_check()
    assert result is False


# ── ingest ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_returns_memory_entries():
    from app.ingestion.plugins.clickhouse import ingest
    rows = [
        {
            "service": "backoffice-gateway-api",
            "operator": "baytreeapp.gameassists.co.uk",
            "total_spans": 5000,
            "error_count": 120,
            "error_rate_pct": 2.4,
            "p95_ms": 350.5,
        }
    ]
    with patch("app.ingestion.plugins.clickhouse.httpx.AsyncClient") as mock_cls, \
         patch("app.ingestion.plugins.clickhouse.get_memory_by_source", return_value=None):
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = _make_ch_response(rows)
        entries = await ingest(since=datetime(2026, 4, 8, 0, 0, 0, tzinfo=timezone.utc))

    assert len(entries) == 1
    e = entries[0]
    assert e.type == "clickhouse"
    assert "baytreeapp" in e.content
    assert "2.4" in e.content or "2.40" in e.content  # error rate
    assert e.source.startswith("clickhouse://")
    assert e.project == "backoffice-gateway-api"


@pytest.mark.asyncio
async def test_ingest_sets_importance_based_on_error_rate():
    """High error rate → higher importance."""
    from app.ingestion.plugins.clickhouse import ingest
    rows_high = [{"service": "svc", "operator": "op", "total_spans": 1000,
                  "error_count": 100, "error_rate_pct": 10.0, "p95_ms": 500.0}]
    rows_low = [{"service": "svc", "operator": "op2", "total_spans": 1000,
                 "error_count": 5, "error_rate_pct": 0.5, "p95_ms": 100.0}]
    all_rows = rows_high + rows_low

    with patch("app.ingestion.plugins.clickhouse.httpx.AsyncClient") as mock_cls, \
         patch("app.ingestion.plugins.clickhouse.get_memory_by_source", return_value=None):
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = _make_ch_response(all_rows)
        entries = await ingest(since=datetime(2026, 4, 8, 0, 0, tzinfo=timezone.utc))

    assert len(entries) == 2
    high_entry = next(e for e in entries if "op" == e.source.split("/")[-1].split("@")[-1] or "op" in e.content)
    low_entry = next(e for e in entries if "op2" in e.content or "op2" in e.source)
    assert high_entry.importance >= low_entry.importance


@pytest.mark.asyncio
async def test_ingest_deduplicates_by_source():
    from app.ingestion.plugins.clickhouse import ingest
    from app.models import MemoryEntry
    rows = [{"service": "svc", "operator": "op", "total_spans": 1000,
             "error_count": 10, "error_rate_pct": 1.0, "p95_ms": 200.0}]

    existing = MemoryEntry(content="x", type="clickhouse", project="svc")
    with patch("app.ingestion.plugins.clickhouse.httpx.AsyncClient") as mock_cls, \
         patch("app.ingestion.plugins.clickhouse.get_memory_by_source", return_value=existing):
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = _make_ch_response(rows)
        entries = await ingest(since=datetime(2026, 4, 8, 0, 0, tzinfo=timezone.utc))

    assert entries == []


@pytest.mark.asyncio
async def test_ingest_returns_empty_on_no_data():
    from app.ingestion.plugins.clickhouse import ingest
    with patch("app.ingestion.plugins.clickhouse.httpx.AsyncClient") as mock_cls, \
         patch("app.ingestion.plugins.clickhouse.get_memory_by_source", return_value=None):
        mock_client = AsyncMock()
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = _make_ch_response([])
        entries = await ingest(since=datetime(2026, 4, 8, 0, 0, tzinfo=timezone.utc))

    assert entries == []
