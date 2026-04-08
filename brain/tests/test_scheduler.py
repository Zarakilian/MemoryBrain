# brain/tests/test_scheduler.py
import pytest
import types
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from app.models import MemoryEntry


def _make_plugin(memory_type: str, schedule_hours: int = 6):
    mod = types.ModuleType(memory_type)
    mod.MEMORY_TYPE = memory_type
    mod.SCHEDULE_HOURS = schedule_hours
    mod.ingest = AsyncMock(return_value=[])
    return mod


@pytest.mark.asyncio
async def test_run_plugin_calls_ingest_with_since(tmp_db):
    plugin = _make_plugin("testplugin")
    expected_since = datetime(2026, 3, 27, 4, 0, 0)

    from app.storage import set_last_run
    set_last_run("testplugin", expected_since, db_path=tmp_db)

    with patch("app.ingestion.scheduler.DB_PATH", tmp_db), \
         patch("app.ingestion.scheduler.ingest_pipeline_ingest", new_callable=AsyncMock):
        from app.ingestion.scheduler import run_plugin
        await run_plugin(plugin)

    plugin.ingest.assert_called_once()
    called_since = plugin.ingest.call_args[0][0]
    assert called_since == expected_since


@pytest.mark.asyncio
async def test_run_plugin_updates_last_run(tmp_db):
    plugin = _make_plugin("myplugin")

    with patch("app.ingestion.scheduler.DB_PATH", tmp_db), \
         patch("app.ingestion.scheduler.ingest_pipeline_ingest", new_callable=AsyncMock):
        from app.ingestion.scheduler import run_plugin
        await run_plugin(plugin)

    from app.storage import get_last_run
    result = get_last_run("myplugin", db_path=tmp_db)
    assert result is not None


@pytest.mark.asyncio
async def test_run_plugin_calls_ingest_pipeline_for_each_entry(tmp_db):
    plugin = _make_plugin("confluence")
    entry1 = MemoryEntry(content="page 1", type="confluence", project="eze", source="http://a")
    entry2 = MemoryEntry(content="page 2", type="confluence", project="eze", source="http://b")
    plugin.ingest = AsyncMock(return_value=[entry1, entry2])

    with patch("app.ingestion.scheduler.DB_PATH", tmp_db), \
         patch("app.ingestion.scheduler.ingest_pipeline_ingest", new_callable=AsyncMock) as mock_ingest:
        from app.ingestion.scheduler import run_plugin
        await run_plugin(plugin)

    assert mock_ingest.call_count == 2


@pytest.mark.asyncio
async def test_run_plugin_uses_schedule_hours_as_default_since(tmp_db):
    """When no last_run is stored, since = now - SCHEDULE_HOURS."""
    plugin = _make_plugin("fresh", schedule_hours=6)

    with patch("app.ingestion.scheduler.DB_PATH", tmp_db), \
         patch("app.ingestion.scheduler.ingest_pipeline_ingest", new_callable=AsyncMock):
        from app.ingestion.scheduler import run_plugin
        await run_plugin(plugin)

    plugin.ingest.assert_called_once()
    called_since = plugin.ingest.call_args[0][0]
    expected_min = datetime.utcnow() - timedelta(hours=7)
    assert called_since > expected_min


def test_start_scheduler_registers_jobs_for_active_plugins():
    p1 = _make_plugin("plugin1", schedule_hours=6)
    p2 = _make_plugin("plugin2", schedule_hours=2)

    mock_scheduler = MagicMock()
    with patch("app.ingestion.scheduler.AsyncIOScheduler", return_value=mock_scheduler):
        from app.ingestion.scheduler import start_scheduler
        result = start_scheduler([p1, p2])

    assert mock_scheduler.add_job.call_count == 2
    mock_scheduler.start.assert_called_once()
    assert result == mock_scheduler


def test_start_scheduler_returns_immediately_with_no_plugins():
    mock_scheduler = MagicMock()
    with patch("app.ingestion.scheduler.AsyncIOScheduler", return_value=mock_scheduler):
        from app.ingestion.scheduler import start_scheduler
        start_scheduler([])

    mock_scheduler.add_job.assert_not_called()
    mock_scheduler.start.assert_called_once()
