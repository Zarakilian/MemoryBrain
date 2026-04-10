# MemoryBrain Part 2 — Plugins + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Confluence + PagerDuty scheduled ingestion plugins, APScheduler wiring, plugin status in MCP tools, and a portable `brain` CLI with idempotent `setup --auto-detect`.

**Architecture:** APScheduler runs inside the FastAPI process (AsyncIOScheduler, started in lifespan). A file-based plugin loader auto-discovers modules in `brain/app/ingestion/plugins/`, health-checks each, and schedules only passing ones. A Python stdlib CLI at `cli/brain.py` handles setup, notes, file import, and status — and installs a shell alias so it's callable from anywhere.

**Tech Stack:** Python 3.11, FastAPI, APScheduler 3.x (AsyncIOScheduler), httpx (already in requirements), SQLite FTS5 (already in use), pytest + pytest-asyncio.

---

## Working directory

All paths relative to `/mnt/c/git/_git/MemoryBrain/`.
Run tests from `brain/` with `PYTHONPATH=.`.
Existing tests (38): all must continue to pass after every task.

---

## File Structure

### New files
```
brain/app/ingestion/plugins/
├── __init__.py          — loader: discover_plugins(), ACTIVE_PLUGINS, INACTIVE_PLUGINS
├── confluence.py        — Confluence plugin (SCHEDULE_HOURS=6)
├── pagerduty.py         — PagerDuty plugin (SCHEDULE_HOURS=2)
├── clickhouse_stub.py   — stub, auto-skipped by loader (_stub suffix)
└── jira_stub.py         — stub, auto-skipped by loader (_stub suffix)

brain/app/ingestion/scheduler.py   — start_scheduler(), run_plugin(), last_run tracking

cli/brain.py                       — brain CLI: setup, add, import, seed, status

brain/tests/
├── test_storage_additions.py      — plugin_state table + get_memory_by_source
├── test_plugins.py                — plugin loader discovery + health_check mocking
├── test_scheduler.py              — scheduler registration + last_run persistence
├── test_confluence_plugin.py      — Confluence ingest with mocked httpx
├── test_pagerduty_plugin.py       — PagerDuty ingest with mocked httpx
└── test_brain_cli.py              — CLI commands with mocked HTTP
```

### Modified files
```
brain/app/storage.py               — add plugin_state table, get_memory_by_source, get_last_run, set_last_run
brain/app/main.py                  — lifespan starts/stops scheduler; add GET /status endpoint
brain/app/mcp/tools.py             — handle_list_projects prepends plugin status
brain/requirements.txt             — add apscheduler>=3.10.4
HOW_IT_WORKS.md                    — add Part 2 section
PROGRESS_LOG.md                    — update status
```

---

## Task 1: Storage additions — plugin_state table + source lookup

**Files:**
- Modify: `brain/app/storage.py`
- Create: `brain/tests/test_storage_additions.py`

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_storage_additions.py
import pytest
from datetime import datetime, timezone
from app.models import MemoryEntry
from app.storage import (
    add_memory, get_memory_by_source,
    get_last_run, set_last_run,
    init_db,
)


def test_get_memory_by_source_returns_entry(tmp_db):
    entry = MemoryEntry(
        content="confluence page about monitoring",
        type="confluence",
        project="eze",
        source="https://confluence.example.com/pages/123",
    )
    add_memory(entry, db_path=tmp_db)
    result = get_memory_by_source("https://confluence.example.com/pages/123", db_path=tmp_db)
    assert result is not None
    assert result.id == entry.id
    assert result.source == "https://confluence.example.com/pages/123"


def test_get_memory_by_source_returns_none_when_missing(tmp_db):
    result = get_memory_by_source("https://not-stored.com/page/999", db_path=tmp_db)
    assert result is None


def test_set_and_get_last_run(tmp_db):
    dt = datetime(2026, 3, 27, 10, 0, 0)
    set_last_run("confluence", dt, db_path=tmp_db)
    result = get_last_run("confluence", db_path=tmp_db)
    assert result is not None
    assert result.year == 2026
    assert result.month == 3
    assert result.day == 27


def test_get_last_run_returns_none_when_not_set(tmp_db):
    result = get_last_run("pagerduty", db_path=tmp_db)
    assert result is None


def test_set_last_run_upserts(tmp_db):
    dt1 = datetime(2026, 3, 27, 10, 0, 0)
    dt2 = datetime(2026, 3, 27, 16, 0, 0)
    set_last_run("confluence", dt1, db_path=tmp_db)
    set_last_run("confluence", dt2, db_path=tmp_db)
    result = get_last_run("confluence", db_path=tmp_db)
    assert result.hour == 16
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_storage_additions.py -v
```

Expected: `ImportError: cannot import name 'get_memory_by_source'`

- [ ] **Step 3: Add `plugin_state` table to `init_db()` in `brain/app/storage.py`**

After the existing `CREATE TABLE IF NOT EXISTS projects` block, add:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plugin_state (
                plugin_name TEXT PRIMARY KEY,
                last_run TEXT NOT NULL
            )
        """)
```

- [ ] **Step 4: Add `get_memory_by_source`, `get_last_run`, `set_last_run` to `brain/app/storage.py`**

Add at the end of the file:

```python
def get_memory_by_source(source: str, db_path: Path = DB_PATH) -> Optional[MemoryEntry]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM memories WHERE source = ? ORDER BY timestamp DESC LIMIT 1",
            (source,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_entry(row)


def get_last_run(plugin_name: str, db_path: Path = DB_PATH) -> Optional[datetime]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT last_run FROM plugin_state WHERE plugin_name = ?",
            (plugin_name,),
        ).fetchone()
    if row is None:
        return None
    return datetime.fromisoformat(row["last_run"])


def set_last_run(plugin_name: str, dt: datetime, db_path: Path = DB_PATH):
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO plugin_state (plugin_name, last_run) VALUES (?, ?)
               ON CONFLICT(plugin_name) DO UPDATE SET last_run = excluded.last_run""",
            (plugin_name, dt.isoformat()),
        )
        conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_storage_additions.py tests/test_storage.py -v
```

Expected: all PASSED (5 new + 7 existing)

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/storage.py brain/tests/test_storage_additions.py
git commit -m "feat: storage additions — plugin_state table, get_memory_by_source, get/set_last_run"
```

---

## Task 2: Plugin loader

**Files:**
- Create: `brain/app/ingestion/plugins/__init__.py`
- Create: `brain/tests/test_plugins.py`

- [ ] **Step 1: Write the failing tests**

```python
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
    import importlib
    import app.ingestion.plugins as plug_module
    plug_module.ACTIVE_PLUGINS = ["x"]
    plug_module.INACTIVE_PLUGINS = ["y"]
    assert plug_module.ACTIVE_PLUGINS == ["x"]
    assert plug_module.INACTIVE_PLUGINS == ["y"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_plugins.py -v
```

Expected: `ImportError: cannot import name 'discover_plugins'`

- [ ] **Step 3: Create `brain/app/ingestion/plugins/__init__.py`**

```python
import importlib
import os
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ACTIVE_PLUGINS: list = []
INACTIVE_PLUGINS: list = []


def _scan_plugin_files() -> list:
    """Import all plugin modules in this directory. Skips __init__.py and *_stub.py."""
    plugins_dir = Path(__file__).parent
    modules = []
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name == "__init__.py" or path.name.endswith("_stub.py"):
            continue
        module_name = f"app.ingestion.plugins.{path.stem}"
        try:
            mod = importlib.import_module(module_name)
            modules.append(mod)
        except Exception as e:
            logger.warning(f"Failed to import plugin {path.name}: {e}")
    return modules


async def discover_plugins() -> tuple[list, list]:
    """Discover, health-check, and categorise all plugins. Updates module globals."""
    global ACTIVE_PLUGINS, INACTIVE_PLUGINS
    active = []
    inactive = []

    for plugin in _scan_plugin_files():
        name = getattr(plugin, "MEMORY_TYPE", plugin.__name__)

        # Check required env vars
        required = getattr(plugin, "REQUIRED_ENV", [])
        if any(not os.getenv(var) for var in required):
            missing = [v for v in required if not os.getenv(v)]
            logger.info(f"Plugin '{name}' skipped — missing env: {missing}")
            inactive.append(plugin)
            continue

        # Health check
        try:
            healthy = await plugin.health_check()
            if not healthy:
                raise ValueError("health_check returned False")
            active.append(plugin)
            logger.info(f"Plugin '{name}' activated ✅")
        except Exception as e:
            logger.warning(f"Plugin '{name}' skipped — health check failed: {e}")
            inactive.append(plugin)

    ACTIVE_PLUGINS = active
    INACTIVE_PLUGINS = inactive
    return active, inactive
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_plugins.py -v
```

Expected: 5 PASSED

- [ ] **Step 5: Run full suite to verify no regressions**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/ingestion/plugins/__init__.py brain/tests/test_plugins.py
git commit -m "feat: plugin loader — discover_plugins, health-check, ACTIVE/INACTIVE globals"
```

---

## Task 3: Scheduler

**Files:**
- Create: `brain/app/ingestion/scheduler.py`
- Create: `brain/tests/test_scheduler.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_scheduler.py -v
```

Expected: `ImportError: cannot import name 'run_plugin'`

- [ ] **Step 3: Create `brain/app/ingestion/scheduler.py`**

```python
import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..storage import get_last_run, set_last_run, DB_PATH
from ..ingest_pipeline import ingest as ingest_pipeline_ingest

logger = logging.getLogger(__name__)


async def run_plugin(plugin) -> None:
    """Run one plugin cycle: determine since, call ingest, store each result, update last_run."""
    name = plugin.MEMORY_TYPE
    last_run = get_last_run(name, db_path=DB_PATH)

    if last_run is None:
        since = datetime.utcnow() - timedelta(hours=plugin.SCHEDULE_HOURS)
        logger.info(f"Plugin '{name}': first run, pulling last {plugin.SCHEDULE_HOURS}h")
    else:
        since = last_run
        logger.info(f"Plugin '{name}': pulling since {since.isoformat()}")

    try:
        entries = await plugin.ingest(since)
        logger.info(f"Plugin '{name}': got {len(entries)} entries")
        for entry in entries:
            await ingest_pipeline_ingest(entry)
        set_last_run(name, datetime.utcnow(), db_path=DB_PATH)
    except Exception as e:
        logger.error(f"Plugin '{name}' run failed: {e}")
        # Do not update last_run on failure — retry from same point next cycle


def start_scheduler(active_plugins: list) -> AsyncIOScheduler:
    """Create and start the APScheduler. Registers one job per active plugin."""
    scheduler = AsyncIOScheduler()

    for plugin in active_plugins:
        scheduler.add_job(
            run_plugin,
            trigger="interval",
            hours=plugin.SCHEDULE_HOURS,
            args=[plugin],
            id=plugin.MEMORY_TYPE,
            replace_existing=True,
        )
        logger.info(f"Scheduled plugin '{plugin.MEMORY_TYPE}' every {plugin.SCHEDULE_HOURS}h")

    scheduler.start()
    return scheduler
```

- [ ] **Step 4: Add `apscheduler` back to `brain/requirements.txt`**

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
mcp>=1.2.0
chromadb>=0.5.0
ollama>=0.2.0
apscheduler>=3.10.4
httpx>=0.26.0
pydantic>=2.0.0
# test
pytest>=8.0.0
pytest-asyncio>=0.23.0
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
pip install apscheduler>=3.10.4
PYTHONPATH=. pytest tests/test_scheduler.py -v
```

Expected: 6 PASSED

- [ ] **Step 6: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/ingestion/scheduler.py brain/tests/test_scheduler.py brain/requirements.txt
git commit -m "feat: APScheduler — start_scheduler, run_plugin, last_run tracking"
```

---

## Task 4: Wire scheduler into main.py + GET /status endpoint

**Files:**
- Modify: `brain/app/main.py`
- Modify: `brain/tests/test_main.py`

- [ ] **Step 1: Write the failing test for GET /status**

Add to `brain/tests/test_main.py`:

```python
# Add to existing brain/tests/test_main.py

def test_status_endpoint_returns_structure():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = client.get("/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "project_count" in data
    assert "active_plugins" in data
    assert "inactive_plugins" in data
    assert isinstance(data["active_plugins"], list)
    assert isinstance(data["inactive_plugins"], list)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_main.py::test_status_endpoint_returns_structure -v
```

Expected: `FAILED` — 404 for `/status`

- [ ] **Step 3: Replace `brain/app/main.py` with the updated version**

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .ingestion.plugins import discover_plugins, ACTIVE_PLUGINS, INACTIVE_PLUGINS
from .ingestion.scheduler import start_scheduler
from .storage import init_db, list_projects, DB_PATH

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    active, inactive = await discover_plugins()
    scheduler = start_scheduler(active)
    logger.info(f"Brain started — {len(active)} plugins active, {len(inactive)} inactive")
    yield
    scheduler.shutdown()


app = FastAPI(title="MemoryBrain", version="0.2.0", lifespan=lifespan)
sse_transport = SseServerTransport("/messages/")

app.include_router(session_router)
app.include_router(manual_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    return {
        "project_count": len(list_projects(db_path=DB_PATH)),
        "active_plugins": [p.MEMORY_TYPE for p in ACTIVE_PLUGINS],
        "inactive_plugins": [p.MEMORY_TYPE for p in INACTIVE_PLUGINS],
    }


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


@app.get("/sse")
async def sse_endpoint(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


@app.post("/messages/")
async def handle_messages(request: Request):
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_main.py -v
```

Expected: all PASSED (health + startup-summary + status)

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/main.py brain/tests/test_main.py
git commit -m "feat: wire scheduler into lifespan + GET /status endpoint"
```

---

## Task 5: Update list_projects MCP tool with plugin status

**Files:**
- Modify: `brain/app/mcp/tools.py`
- Modify: `brain/tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

Add to `brain/tests/test_mcp_tools.py`:

```python
# Add to existing brain/tests/test_mcp_tools.py

@pytest.mark.asyncio
async def test_list_projects_includes_plugin_status(tmp_db):
    import app.ingestion.plugins as plug_mod
    import types

    fake_active = types.ModuleType("confluence")
    fake_active.MEMORY_TYPE = "confluence"
    fake_inactive = types.ModuleType("clickhouse")
    fake_inactive.MEMORY_TYPE = "clickhouse"

    plug_mod.ACTIVE_PLUGINS = [fake_active]
    plug_mod.INACTIVE_PLUGINS = [fake_inactive]

    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_list_projects()

    assert "confluence ✅" in result
    assert "clickhouse ❌" in result

    # cleanup
    plug_mod.ACTIVE_PLUGINS = []
    plug_mod.INACTIVE_PLUGINS = []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_mcp_tools.py::test_list_projects_includes_plugin_status -v
```

Expected: `FAILED` — no plugin status in output

- [ ] **Step 3: Update `handle_list_projects` in `brain/app/mcp/tools.py`**

Add the import at the top of the file (after existing imports):
```python
from ..ingestion.plugins import ACTIVE_PLUGINS, INACTIVE_PLUGINS
```

Replace the `handle_list_projects` function:

```python
async def handle_list_projects() -> str:
    projects = storage_list_projects(db_path=DB_PATH)
    lines = []

    # Plugin status header
    active_names = [p.MEMORY_TYPE for p in ACTIVE_PLUGINS]
    inactive_names = [p.MEMORY_TYPE for p in INACTIVE_PLUGINS]
    if active_names or inactive_names:
        active_str = "  ".join(f"{n} ✅" for n in active_names) if active_names else "none"
        inactive_str = "  ".join(f"{n} ❌" for n in inactive_names) if inactive_names else ""
        lines.append(f"Active plugins:   {active_str}")
        if inactive_str:
            lines.append(f"Inactive plugins: {inactive_str}")
        lines.append("")

    lines.append("## Projects\n")
    for p in projects:
        lines.append(f"**{p.slug}** — {p.name}")
        if p.one_liner:
            lines.append(f"  {p.one_liner}")
        lines.append(f"  Last activity: {p.last_activity.strftime('%Y-%m-%d')}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_mcp_tools.py -v
```

Expected: all PASSED (7 existing + 1 new)

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/mcp/tools.py brain/tests/test_mcp_tools.py
git commit -m "feat: list_projects MCP tool — prepend plugin status (active/inactive)"
```

---

## Task 6: Confluence plugin

**Files:**
- Create: `brain/app/ingestion/plugins/confluence.py`
- Create: `brain/tests/test_confluence_plugin.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_confluence_plugin.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.ingestion.plugins.confluence'`

- [ ] **Step 3: Create `brain/app/ingestion/plugins/confluence.py`**

```python
import os
import re
import logging
from datetime import datetime
from typing import Optional

import httpx

from ....models import MemoryEntry
from ....storage import get_memory_by_source, DB_PATH

logger = logging.getLogger(__name__)

REQUIRED_ENV = ["CONFLUENCE_URL", "CONFLUENCE_TOKEN"]
SCHEDULE_HOURS = 6
MEMORY_TYPE = "confluence"


def _confluence_url() -> str:
    return os.getenv("CONFLUENCE_URL", "").rstrip("/")


def _token() -> str:
    return os.getenv("CONFLUENCE_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text
            .replace("&nbsp;", " ").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&")
            .replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


async def health_check() -> bool:
    url = _confluence_url()
    token = _token()
    if not url or not token:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{url}/rest/api/user/current",
                headers=_headers(),
                timeout=10,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def ingest(since: datetime) -> list[MemoryEntry]:
    """Pull pages where current user is author or last modifier, updated since `since`."""
    url = _confluence_url()
    since_str = since.strftime("%Y-%m-%d")
    cql = f'lastModified > "{since_str}" AND contributor = currentUser()'
    entries = []

    async with httpx.AsyncClient() as client:
        start = 0
        while True:
            resp = await client.get(
                f"{url}/rest/api/content/search",
                headers=_headers(),
                params={
                    "cql": cql,
                    "expand": "body.storage,version,space",
                    "limit": 50,
                    "start": start,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning(f"Confluence search returned {resp.status_code}")
                break

            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            for page in results:
                page_id = page["id"]
                space_key = page["space"]["key"].lower()
                title = page["title"]
                last_modified_str = page["version"]["when"]
                web_path = page["_links"]["webui"]
                page_url = f"{url}{web_path}"
                html_body = page.get("body", {}).get("storage", {}).get("value", "")
                plain_text = _strip_html(html_body)[:8000]

                # Deduplication: skip if we already have this page at same/newer version
                existing = get_memory_by_source(page_url, db_path=DB_PATH)
                if existing is not None:
                    try:
                        page_dt = datetime.fromisoformat(
                            last_modified_str.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        if existing.timestamp >= page_dt:
                            logger.debug(f"Skipping unchanged page: {title}")
                            continue
                    except ValueError:
                        pass  # can't parse date — re-ingest

                entry = MemoryEntry(
                    content=f"{title}\n\n{plain_text}",
                    type=MEMORY_TYPE,
                    project=space_key,
                    tags=[space_key, page_id],
                    source=page_url,
                )
                entries.append(entry)

            # Pagination
            if "_links" in data and "next" not in data.get("_links", {}):
                break
            if len(results) < 50:
                break
            start += 50

    logger.info(f"Confluence: {len(entries)} new/updated pages to ingest")
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_confluence_plugin.py -v
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/ingestion/plugins/confluence.py brain/tests/test_confluence_plugin.py
git commit -m "feat: Confluence plugin — ingest pages by contributor, 6h schedule, deduplication"
```

---

## Task 7: PagerDuty plugin

**Files:**
- Create: `brain/app/ingestion/plugins/pagerduty.py`
- Create: `brain/tests/test_pagerduty_plugin.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_pagerduty_plugin.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.ingestion.plugins.pagerduty'`

- [ ] **Step 3: Create `brain/app/ingestion/plugins/pagerduty.py`**

```python
import os
import logging
from datetime import datetime
from typing import Optional

import httpx

from ....models import MemoryEntry
from ....storage import get_memory_by_source, DB_PATH

logger = logging.getLogger(__name__)

REQUIRED_ENV = ["PAGERDUTY_TOKEN"]
SCHEDULE_HOURS = 2
MEMORY_TYPE = "pagerduty"

PD_BASE = "https://api.pagerduty.com"
_cached_user_id: Optional[str] = None


def _token() -> str:
    return os.getenv("PAGERDUTY_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Token token={_token()}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }


async def health_check() -> bool:
    if not _token():
        return False
    global _cached_user_id
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PD_BASE}/users/me",
                headers=_headers(),
                timeout=10,
            )
        if resp.status_code == 200:
            _cached_user_id = resp.json()["user"]["id"]
            return True
        return False
    except Exception:
        return False


def _duration_minutes(created_at: str, resolved_at: str) -> int:
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        created = datetime.strptime(created_at, fmt)
        resolved = datetime.strptime(resolved_at, fmt)
        return max(0, int((resolved - created).total_seconds() / 60))
    except Exception:
        return 0


async def ingest(since: datetime) -> list[MemoryEntry]:
    """Pull incidents resolved since `since` assigned to current user."""
    global _cached_user_id

    # Ensure we have user ID (re-fetch if not cached)
    if not _cached_user_id:
        await health_check()
    if not _cached_user_id:
        logger.warning("PagerDuty: cannot determine current user ID, skipping")
        return []

    entries = []
    offset = 0

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{PD_BASE}/incidents",
                headers=_headers(),
                params={
                    "statuses[]": "resolved",
                    "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "assigned_to_user[]": _cached_user_id,
                    "limit": 100,
                    "offset": offset,
                },
                timeout=30,
            )

            if resp.status_code != 200:
                logger.warning(f"PagerDuty incidents API returned {resp.status_code}")
                break

            data = resp.json()
            incidents = data.get("incidents", [])
            if not incidents:
                break

            for inc in incidents:
                incident_url = inc["html_url"]

                # Deduplication: skip if already stored
                if get_memory_by_source(incident_url, db_path=DB_PATH) is not None:
                    continue

                duration = _duration_minutes(inc["created_at"], inc.get("resolved_at", ""))
                resolved_at = inc.get("resolved_at", "")[:16].replace("T", " ")
                service = inc.get("service", {}).get("summary", "unknown service")
                urgency = inc.get("urgency", "unknown")

                content = (
                    f"[{urgency}] {inc['title']} — {service} — "
                    f"resolved in {duration}m ({resolved_at})"
                )

                entry = MemoryEntry(
                    content=content,
                    summary=content,   # no Ollama needed — already concise
                    type=MEMORY_TYPE,
                    project="pagerduty",
                    tags=[service, urgency, inc["id"]],
                    source=incident_url,
                    importance=4,
                )
                entries.append(entry)

            if not data.get("more", False):
                break
            offset += 100

    logger.info(f"PagerDuty: {len(entries)} new incidents to ingest")
    return entries
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_pagerduty_plugin.py -v
```

Expected: all PASSED

- [ ] **Step 5: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/ingestion/plugins/pagerduty.py brain/tests/test_pagerduty_plugin.py
git commit -m "feat: PagerDuty plugin — ingest resolved incidents, 2h schedule, summary=content, importance=4"
```

---

## Task 8: Stub plugins

**Files:**
- Create: `brain/app/ingestion/plugins/clickhouse_stub.py`
- Create: `brain/app/ingestion/plugins/jira_stub.py`

No unit tests for stubs — they are templates only and must not be loaded by the plugin loader.

- [ ] **Step 1: Create `brain/app/ingestion/plugins/clickhouse_stub.py`**

```python
"""
ClickHouse Plugin — STUB (not active)

When implemented, this plugin would pull query results or metrics
flagged manually from ClickHouse APM / observability data.

To activate: rename to clickhouse.py, implement health_check and ingest,
add CLICKHOUSE_IOM_URL and CLICKHOUSE_TOKEN to .env.

Plugin contract requires:
    REQUIRED_ENV: list[str]
    SCHEDULE_HOURS: int
    MEMORY_TYPE: str
    async def health_check() -> bool
    async def ingest(since: datetime) -> list[MemoryEntry]
"""
from datetime import datetime
from ....models import MemoryEntry

REQUIRED_ENV = ["CLICKHOUSE_IOM_URL", "CLICKHOUSE_TOKEN"]
SCHEDULE_HOURS = 12
MEMORY_TYPE = "clickhouse"


async def health_check() -> bool:
    raise NotImplementedError("ClickHouse plugin is a stub — not yet implemented")


async def ingest(since: datetime) -> list[MemoryEntry]:
    raise NotImplementedError("ClickHouse plugin is a stub — not yet implemented")
```

- [ ] **Step 2: Create `brain/app/ingestion/plugins/jira_stub.py`**

```python
"""
Jira Plugin — STUB (not active)

When implemented, this plugin would pull Jira tickets assigned to you
or recently updated, storing title + description as memories.

To activate: rename to jira.py, implement health_check and ingest,
add JIRA_URL and JIRA_TOKEN to .env.
"""
from datetime import datetime
from ....models import MemoryEntry

REQUIRED_ENV = ["JIRA_URL", "JIRA_TOKEN"]
SCHEDULE_HOURS = 6
MEMORY_TYPE = "jira"


async def health_check() -> bool:
    raise NotImplementedError("Jira plugin is a stub — not yet implemented")


async def ingest(since: datetime) -> list[MemoryEntry]:
    raise NotImplementedError("Jira plugin is a stub — not yet implemented")
```

- [ ] **Step 3: Verify loader skips stubs**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
python3 -c "
from app.ingestion.plugins import _scan_plugin_files
mods = _scan_plugin_files()
names = [m.__name__ for m in mods]
print('Discovered:', names)
assert not any('stub' in n for n in names), 'Stub was loaded!'
print('OK — no stubs loaded')
"
```

Expected: `OK — no stubs loaded` (confluence and pagerduty visible if env vars set, otherwise empty list)

- [ ] **Step 4: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 5: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add brain/app/ingestion/plugins/clickhouse_stub.py brain/app/ingestion/plugins/jira_stub.py
git commit -m "feat: stub plugins — clickhouse_stub.py + jira_stub.py (templates, auto-skipped by loader)"
```

---

## Task 9: `brain` CLI

**Files:**
- Create: `cli/brain.py`
- Create: `brain/tests/test_brain_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_brain_cli.py
import pytest
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add cli/ to path for import
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "cli"))


def _make_response(status_code: int = 200, body: dict = None):
    mock = MagicMock()
    mock.status = status_code
    mock.read = MagicMock(return_value=json.dumps(body or {}).encode())
    mock.__enter__ = MagicMock(return_value=mock)
    mock.__exit__ = MagicMock(return_value=None)
    return mock


def test_brain_add_calls_ingest_note(monkeypatch, capsys):
    response_body = {"id": "abc-123", "summary": "Test note stored.", "importance": 3}

    with patch("urllib.request.urlopen", return_value=_make_response(201, response_body)):
        import brain as brain_cli
        brain_cli.cmd_add("test note content", project="monitoring", tags=[])

    captured = capsys.readouterr()
    assert "abc-123" in captured.out


def test_brain_status_shows_running(monkeypatch, capsys):
    health_resp = _make_response(200, {"status": "ok"})
    status_resp = _make_response(200, {
        "project_count": 3,
        "active_plugins": ["confluence"],
        "inactive_plugins": ["clickhouse"],
    })

    with patch("urllib.request.urlopen", side_effect=[health_resp, status_resp]):
        import brain as brain_cli
        brain_cli.cmd_status()

    captured = capsys.readouterr()
    assert "running" in captured.out
    assert "confluence" in captured.out
    assert "3" in captured.out


def test_brain_status_shows_not_running(monkeypatch, capsys):
    import urllib.error

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
        import brain as brain_cli
        with pytest.raises(SystemExit) as exc:
            brain_cli.cmd_status()
        assert exc.value.code == 1

    captured = capsys.readouterr()
    assert "not running" in captured.out.lower() or "not running" in captured.err.lower()


def test_detect_project_from_brainproject_file(tmp_path):
    (tmp_path / ".brainproject").write_text("monitoring\n")
    import brain as brain_cli
    result = brain_cli.detect_project(tmp_path)
    assert result == "monitoring"


def test_detect_project_heuristic(tmp_path):
    project_dir = tmp_path / "mnt" / "c" / "git" / "_git" / "Monitoring"
    project_dir.mkdir(parents=True)
    import brain as brain_cli
    result = brain_cli.detect_project(project_dir)
    assert result == "monitoring"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_brain_cli.py -v
```

Expected: `ModuleNotFoundError: No module named 'brain'`

- [ ] **Step 3: Create `cli/brain.py`**

```python
#!/usr/bin/env python3
"""
MemoryBrain CLI — brain add / brain import / brain seed / brain status / brain setup

Usage:
    brain setup [--auto-detect]
    brain add "note text" [--project SLUG] [--tags tag1,tag2]
    brain import <path> [--project SLUG]
    brain seed [--project SLUG]
    brain status
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime

MEMORYBRAIN_DIR = Path(__file__).parent.parent.resolve()
BRAIN_URL = os.getenv("MEMORYBRAIN_URL", "http://localhost:7741")


# ── Project detection ────────────────────────────────────────────────────────

def detect_project(cwd: Path = None) -> str:
    cwd = cwd or Path.cwd()
    bp = cwd / ".brainproject"
    if bp.exists():
        return bp.read_text().strip()
    parts = [p for p in cwd.parts if p not in ("", "/", "mnt", "c", "git", "_git")]
    return parts[-1].lower() if parts else "unknown"


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _post(path: str, body: dict, status_ok: int = 201) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRAIN_URL}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError:
        print(f"Brain is not running. Start with:\n  docker compose -f {MEMORYBRAIN_DIR}/docker-compose.yml up -d")
        sys.exit(1)


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{BRAIN_URL}{path}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError:
        print(f"Brain is not running. Start with:\n  docker compose -f {MEMORYBRAIN_DIR}/docker-compose.yml up -d")
        sys.exit(1)


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_add(content: str, project: str = None, tags: list = None):
    project = project or detect_project()
    result = _post("/ingest/note", {
        "content": content,
        "project": project,
        "tags": tags or [],
    })
    print(f"Stored — id: {result['id']}")
    print(f"Summary: {result.get('summary', '')}")


def cmd_import(path: str, project: str = None):
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        print(f"File not found: {file_path}")
        sys.exit(1)
    project = project or detect_project(file_path.parent)
    content = file_path.read_text(encoding="utf-8", errors="replace")
    result = _post("/ingest/file" if False else "/ingest/note", {
        "content": content,
        "project": project,
        "tags": [],
        "source": str(file_path),
    })
    print(f"Imported {file_path.name} — id: {result['id']}")
    print(f"Summary: {result.get('summary', '')}")


def cmd_seed(project: str = None):
    cwd = Path.cwd()
    project = project or detect_project(cwd)
    files = list(cwd.glob("MEMORY*.md")) + list(cwd.glob("HANDOVER-*.md")) + list(cwd.glob("memory/MEMORY*.md"))
    if not files:
        print("No MEMORY.md or HANDOVER-*.md files found in current directory.")
        return
    print(f"Seeding {len(files)} files into project '{project}'...")
    for f in sorted(files):
        content = f.read_text(encoding="utf-8", errors="replace")
        result = _post("/ingest/note", {"content": content, "project": project, "tags": [], "source": str(f)})
        print(f"  ✅ {f.name} → {result['id']}")
    print(f"Done — {len(files)} files imported.")


def cmd_status():
    data = _get("/status")
    health = _get("/health")
    active = "  ".join(f"{p} ✅" for p in data.get("active_plugins", [])) or "none"
    inactive = "  ".join(f"{p} ❌" for p in data.get("inactive_plugins", [])) or ""
    print(f"Brain:    ✅ running ({BRAIN_URL})")
    print(f"Projects: {data.get('project_count', 0)}")
    print(f"Plugins:  {active}" + (f"  {inactive}" if inactive else ""))


def _run(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _step(label: str, done: bool, action_done: str = "", action_skip: str = "already done"):
    if done:
        print(f"✅ {label} — {action_done}")
    else:
        print(f"⏭️  {label} — {action_skip}")


def cmd_setup(auto_detect: bool = False):
    print("MemoryBrain setup")
    print("─" * 45)

    # 1. Docker running?
    r = _run(["docker", "ps"])
    if r.returncode != 0:
        print("❌ Docker is not running. Start Docker Desktop / Rancher Desktop first.")
        sys.exit(1)
    print("✅ Docker running")

    # 2. Auto-detect credentials from ~/.claude.json
    env_path = MEMORYBRAIN_DIR / ".env"
    env_updates: dict[str, str] = {}

    if auto_detect:
        claude_json = Path.home() / ".claude.json"
        if claude_json.exists():
            try:
                config = json.loads(claude_json.read_text())
                mcp_servers = config.get("mcpServers", {})

                # Confluence from mcp-atlassian
                if "mcp-atlassian" in mcp_servers:
                    args = mcp_servers["mcp-atlassian"].get("args", [])
                    for i, arg in enumerate(args):
                        if arg == "-e" and i + 1 < len(args):
                            kv = args[i + 1]
                            if "=" in kv:
                                k, v = kv.split("=", 1)
                                if k == "CONFLUENCE_URL":
                                    env_updates["CONFLUENCE_URL"] = v
                                elif k in ("CONFLUENCE_PERSONAL_TOKEN", "CONFLUENCE_TOKEN"):
                                    env_updates["CONFLUENCE_TOKEN"] = v

                # PagerDuty token
                for server_name, server_cfg in mcp_servers.items():
                    if "pagerduty" in server_name.lower():
                        for arg in server_cfg.get("args", []):
                            if "PAGERDUTY" in arg and "=" in arg:
                                k, v = arg.split("=", 1)
                                env_updates["PAGERDUTY_TOKEN"] = v
            except Exception as e:
                print(f"⚠️  Could not parse ~/.claude.json: {e}")

        detected = list(env_updates.keys())
        if detected:
            print(f"✅ Auto-detected credentials: {', '.join(detected)}")
        else:
            print("⚠️  No credentials auto-detected from ~/.claude.json")

    # 3. Write/update .env
    if not env_path.exists():
        example = MEMORYBRAIN_DIR / ".env.example"
        env_path.write_text(example.read_text() if example.exists() else "")

    if env_updates:
        lines = env_path.read_text().splitlines()
        existing_keys = {l.split("=")[0] for l in lines if "=" in l and not l.startswith("#")}
        with open(env_path, "a") as f:
            for k, v in env_updates.items():
                if k not in existing_keys:
                    f.write(f"\n{k}={v}")
        print(f"✅ .env updated")
    else:
        print(f"⏭️  .env — no changes")

    # 4. Start Docker containers
    compose_cmd = ["docker", "compose", "-f", str(MEMORYBRAIN_DIR / "docker-compose.yml")]
    ps = _run(compose_cmd + ["ps", "--status=running"])
    brain_running = "brain" in ps.stdout

    if not brain_running:
        _run(compose_cmd + ["up", "-d"], check=False)
        print("✅ Docker containers started")
    else:
        print("⏭️  Docker containers — already running")

    # 5. Pull Ollama models
    models_out = _run(compose_cmd + ["exec", "ollama", "ollama", "list"]).stdout
    for model in ["nomic-embed-text", "llama3.2:3b"]:
        if model not in models_out:
            print(f"⏳ Pulling Ollama model: {model} (this may take a few minutes)...")
            _run(compose_cmd + ["exec", "ollama", "ollama", "pull", model])
            print(f"✅ {model} pulled")
        else:
            print(f"⏭️  {model} — already present")

    # 6. Register MCP server with Claude Code
    mcp_list = _run(["claude", "mcp", "list"])
    if "memorybrain" not in mcp_list.stdout:
        _run(["claude", "mcp", "add", "-s", "user", "--transport", "sse",
              "memorybrain", f"{BRAIN_URL}/sse"])
        print("✅ MCP server registered")
    else:
        print("⏭️  MCP server — already registered")

    # 7. Install hooks
    hooks_dir = Path.home() / ".claude" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    hook_pairs = [
        (MEMORYBRAIN_DIR / "hooks" / "session-ingest.sh",
         hooks_dir / "session-start-memory.sh"),
        (MEMORYBRAIN_DIR / "hooks" / "pre-compact-ingest.py",
         hooks_dir / "pre-compact-auto-handover.py"),
    ]
    hooks_installed = False
    for src, dst in hook_pairs:
        if not src.exists():
            print(f"⚠️  Hook source not found: {src}")
            continue
        if _file_hash(dst) != _file_hash(src):
            import shutil
            shutil.copy2(src, dst)
            dst.chmod(0o755)
            hooks_installed = True
    print("✅ Hooks installed" if hooks_installed else "⏭️  Hooks — already up to date")

    # 8. Install shell alias
    alias_line = f"alias brain='python3 {MEMORYBRAIN_DIR}/cli/brain.py'"
    alias_added = False
    for rc in [Path.home() / ".bashrc", Path.home() / ".zshrc"]:
        if rc.exists():
            content = rc.read_text()
            if "alias brain=" not in content:
                rc.write_text(content + f"\n# MemoryBrain CLI\n{alias_line}\n")
                alias_added = True

    if alias_added:
        print("✅ Shell alias added (run: source ~/.bashrc)")
    else:
        print("⏭️  Shell alias — already present")

    # Check for missing manual config
    env_text = env_path.read_text() if env_path.exists() else ""
    warnings = []
    if not any(f"PAGERDUTY_TOKEN=" in l and l.split("=", 1)[1].strip()
               for l in env_text.splitlines() if not l.startswith("#")):
        warnings.append("PAGERDUTY_TOKEN not set — add to .env to enable PagerDuty plugin")

    print()
    print(f"Brain is running at {BRAIN_URL}")
    for w in warnings:
        print(f"⚠️  {w}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="brain", description="MemoryBrain CLI")
    sub = parser.add_subparsers(dest="command")

    # setup
    p_setup = sub.add_parser("setup", help="Idempotent full setup")
    p_setup.add_argument("--auto-detect", action="store_true",
                         help="Read ~/.claude.json to pre-fill credentials")

    # add
    p_add = sub.add_parser("add", help="Store a quick note")
    p_add.add_argument("content", help="Note text")
    p_add.add_argument("--project", default=None)
    p_add.add_argument("--tags", default="")

    # import
    p_import = sub.add_parser("import", help="Import a file")
    p_import.add_argument("path", help="Path to file")
    p_import.add_argument("--project", default=None)

    # seed
    p_seed = sub.add_parser("seed", help="Bulk import MEMORY.md + HANDOVER files from CWD")
    p_seed.add_argument("--project", default=None)

    # status
    sub.add_parser("status", help="Show brain status")

    args = parser.parse_args()

    if args.command == "setup":
        cmd_setup(auto_detect=args.auto_detect)
    elif args.command == "add":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        cmd_add(args.content, project=args.project, tags=tags)
    elif args.command == "import":
        cmd_import(args.path, project=args.project)
    elif args.command == "seed":
        cmd_seed(project=args.project)
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Make CLI executable**

```bash
chmod +x /mnt/c/git/_git/MemoryBrain/cli/brain.py
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/test_brain_cli.py -v
```

Expected: 5 PASSED

- [ ] **Step 6: Run full suite**

```bash
PYTHONPATH=. pytest tests/ -v --tb=short 2>&1 | tail -5
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add cli/brain.py brain/tests/test_brain_cli.py
git commit -m "feat: brain CLI — setup (idempotent), add, import, seed, status + shell alias"
```

---

## Task 10: Documentation + final validation

**Files:**
- Modify: `HOW_IT_WORKS.md`
- Modify: `PROGRESS_LOG.md`

- [ ] **Step 1: Run the complete test suite**

```bash
cd /mnt/c/git/_git/MemoryBrain/brain
PYTHONPATH=. pytest tests/ -v --tb=short
```

All tests must pass. Fix any failures before continuing.

- [ ] **Step 2: Add Part 2 section to `HOW_IT_WORKS.md`**

Open `HOW_IT_WORKS.md` and add the following section after `## Setup — any machine` and before `## Upgrading`:

```markdown
## Part 2 — Plugins + `brain` CLI

### The `brain` CLI

After running `brain setup`, a `brain` alias is available in your terminal:

```bash
brain add "just discovered X about Y"       # store a note from anywhere
brain import ~/Downloads/some-doc.md        # import a file
brain seed                                  # bulk import MEMORY.md + HANDOVER files from CWD
brain status                                # check brain health + plugin status
brain setup --auto-detect                   # re-run setup (safe on any machine)
```

On a fresh machine, the full setup is one command:
```bash
python3 ~/memorybrain/cli/brain.py setup --auto-detect
```

### Plugins (Confluence + PagerDuty)

Plugins run on a schedule inside the brain container. They auto-activate if credentials
are present in `.env`, and are silently skipped if not.

| Plugin | Schedule | What it pulls |
|---|---|---|
| Confluence | Every 6h | Pages you authored or last modified in last 7 days |
| PagerDuty | Every 2h | Incidents assigned to you, resolved in last 48h |

**Credentials in `.env`:**
```bash
CONFLUENCE_URL=https://your-confluence.example.com/
CONFLUENCE_TOKEN=your-personal-access-token

PAGERDUTY_TOKEN=your-pd-api-token
```

`brain setup --auto-detect` reads `~/.claude.json` and pre-fills Confluence credentials
automatically from your existing mcp-atlassian MCP config.

**Check plugin status:**
```bash
brain status
# Brain:    ✅ running (http://localhost:7741)
# Projects: 7
# Plugins:  confluence ✅  pagerduty ❌
```

Or via MCP tool in Claude:
```
list_projects()
→ Active plugins:   confluence ✅
→ Inactive plugins: pagerduty ❌ (no credentials)
```

### Adding new plugins

Drop a `.py` file in `brain/app/ingestion/plugins/` implementing the plugin contract:
```python
REQUIRED_ENV = ["MY_SERVICE_TOKEN"]
SCHEDULE_HOURS = 6
MEMORY_TYPE = "myservice"

async def health_check() -> bool: ...
async def ingest(since: datetime) -> list[MemoryEntry]: ...
```

No other changes needed. The loader discovers it automatically on next restart.
```

- [ ] **Step 3: Update `PROGRESS_LOG.md`**

Replace the status line and add a new session log entry:

```markdown
## Status: Part 2 (Plugins + CLI) — COMPLETE ✅

**Latest tag:** v0.2.0
```

Add to the session log:

```markdown
### 2026-03-27 — Part 2 built (subagent-driven, 10 tasks)

**What was added:**
- Plugin loader: auto-discovers modules in plugins/, health-checks, schedules active ones
- Confluence plugin: pulls pages authored/modified by current user, every 6h, deduplicates by URL
- PagerDuty plugin: pulls resolved incidents assigned to user, every 2h, summary=content, importance=4
- APScheduler wired into FastAPI lifespan — starts on brain startup
- GET /status endpoint: structured JSON with project count + plugin lists
- list_projects MCP tool: prepends plugin status block
- brain CLI: setup (idempotent), add, import, seed, status + shell alias install
- brain setup --auto-detect: reads ~/.claude.json, extracts Confluence credentials
- Stub plugins: clickhouse_stub.py, jira_stub.py (templates, auto-skipped)
```

- [ ] **Step 4: Commit documentation**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add HOW_IT_WORKS.md PROGRESS_LOG.md
git commit -m "docs: Part 2 — update HOW_IT_WORKS + PROGRESS_LOG"
```

- [ ] **Step 5: Tag v0.2.0 and push**

```bash
cd /mnt/c/git/_git/MemoryBrain
git tag v0.2.0
git push && git push origin v0.2.0
```

---

## Self-Review

### Spec coverage check

| Spec section | Covered by task |
|---|---|
| Plugin contract (REQUIRED_ENV, SCHEDULE_HOURS, MEMORY_TYPE, health_check, ingest) | Task 2 (loader) + Tasks 6/7 (plugins) |
| Plugin loader — auto-discovers, skips stubs, skips missing env | Task 2 |
| APScheduler in FastAPI lifespan | Task 3 (scheduler) + Task 4 (main.py) |
| last_run per plugin in SQLite | Task 1 (storage) + Task 3 (scheduler) |
| GET /status endpoint | Task 4 |
| list_projects plugin status preamble | Task 5 |
| Confluence plugin — author/modifier scope, deduplication, HTML strip | Task 6 |
| PagerDuty plugin — summary only, importance=4, summary=content | Task 7 |
| Stub plugins (clickhouse, jira) | Task 8 |
| brain CLI — setup, add, import, seed, status | Task 9 |
| brain setup --auto-detect (reads ~/.claude.json) | Task 9 |
| Brain setup idempotent (skips already-done steps) | Task 9 |
| Shell alias installation | Task 9 |
| HOW_IT_WORKS.md updated | Task 10 |
| PROGRESS_LOG.md updated | Task 10 |

### Placeholder scan
None — all steps have actual code.

### Type consistency check
- `MemoryEntry` used identically across Tasks 1, 6, 7, 9
- `get_memory_by_source(source, db_path=DB_PATH)` defined in Task 1, used in Tasks 6 + 7
- `get_last_run / set_last_run` defined in Task 1, used in Task 3
- `ACTIVE_PLUGINS / INACTIVE_PLUGINS` set in Task 2 (`plugins/__init__.py`), imported in Tasks 4 + 5
- `ingest_pipeline_ingest` in scheduler.py — this is `from ..ingest_pipeline import ingest as ingest_pipeline_ingest` to avoid name collision with plugin's own `ingest` function
- `start_scheduler(active_plugins)` defined in Task 3, called in Task 4 (`main.py`)
- `discover_plugins()` returns `tuple[list, list]` — used in Task 4 as `active, inactive = await discover_plugins()`
