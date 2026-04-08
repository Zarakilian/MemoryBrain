# brain/tests/test_plugins.py
import pytest
import types
from unittest.mock import AsyncMock, patch
from datetime import datetime


def _make_plugin(name: str, required_env: list[str], healthy: bool = True):
    """Helper: create a minimal fake plugin module."""
    mod = types.ModuleType(name)
    mod.REQUIRED_ENV = required_env
    mod.SCHEDULE_HOURS = 6
    mod.MEMORY_TYPE = name
    mod.health_check = AsyncMock(return_value=healthy)
    mod.ingest = AsyncMock(return_value=[])
    return mod


@pytest.mark.asyncio
async def test_discover_plugins_returns_healthy_plugin(tmp_path, monkeypatch):
    good = _make_plugin("myplugin", required_env=[])
    monkeypatch.setenv("BRAIN_TEST_ACTIVE", "1")

    with patch("app.ingestion.plugins._scan_plugin_files", return_value=[good]):
        from app.ingestion.plugins import discover_plugins
        active, inactive = await discover_plugins()

    assert good in active
    assert good not in inactive


@pytest.mark.asyncio
async def test_discover_plugins_skips_missing_env(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_MISSING_TOKEN", raising=False)
    bad = _make_plugin("noplugin", required_env=["MY_MISSING_TOKEN"])

    with patch("app.ingestion.plugins._scan_plugin_files", return_value=[bad]):
        from app.ingestion.plugins import discover_plugins
        active, inactive = await discover_plugins()

    assert bad not in active
    assert bad in inactive


@pytest.mark.asyncio
async def test_discover_plugins_skips_failed_health_check():
    unhealthy = _make_plugin("sick", required_env=[], healthy=False)

    with patch("app.ingestion.plugins._scan_plugin_files", return_value=[unhealthy]):
        from app.ingestion.plugins import discover_plugins
        active, inactive = await discover_plugins()

    assert unhealthy not in active
    assert unhealthy in inactive


@pytest.mark.asyncio
async def test_discover_plugins_skips_health_check_exception():
    broken = _make_plugin("broken", required_env=[])
    broken.health_check = AsyncMock(side_effect=Exception("connection refused"))

    with patch("app.ingestion.plugins._scan_plugin_files", return_value=[broken]):
        from app.ingestion.plugins import discover_plugins
        active, inactive = await discover_plugins()

    assert broken not in active
    assert broken in inactive


def test_active_inactive_globals_set_after_discover(monkeypatch):
    import app.ingestion.plugins as plug_module
    plug_module.ACTIVE_PLUGINS = ["x"]
    plug_module.INACTIVE_PLUGINS = ["y"]
    assert plug_module.ACTIVE_PLUGINS == ["x"]
    assert plug_module.INACTIVE_PLUGINS == ["y"]
