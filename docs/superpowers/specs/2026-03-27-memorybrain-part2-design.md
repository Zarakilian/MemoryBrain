# MemoryBrain Part 2 — Plugins + CLI Design Spec

**Date:** 2026-03-27
**Author:** migueler + Claude
**Status:** Approved — ready for implementation planning
**Builds on:** Part 1 (Core) — v0.1.0, all 38 tests passing

---

## 1. Scope

Part 2 adds three things to the working Part 1 core:

1. **Plugin system** — auto-discovered, scheduled external data ingestion (Confluence + PagerDuty)
2. **`brain` CLI** — command-line tool for setup, notes, file import, and status
3. **`brain setup --auto-detect`** — idempotent full setup command; the primary portability mechanism

**Out of scope (Part 3 or later):**
- ClickHouse plugin (stub file only, not scheduled)
- Jira plugin (stub file only, not scheduled)
- Web UI
- Cross-machine data sync

---

## 2. Plugin System

### 2.1 Plugin contract

Every plugin is a Python module in `brain/app/ingestion/plugins/`. Each module exposes:

```python
REQUIRED_ENV: list[str]    # env var names that must be non-empty for this plugin to activate
SCHEDULE_HOURS: int        # run interval in hours, e.g. 6 or 2 (passed to APScheduler IntervalTrigger)
MEMORY_TYPE: str           # type tag applied to all MemoryEntry objects from this plugin

async def health_check() -> bool:
    """Return True if credentials are present and the remote endpoint is reachable."""

async def ingest(since: datetime) -> list[MemoryEntry]:
    """Pull new/updated entries since `since`. Return list of MemoryEntry objects.
    The caller (scheduler) runs these through the ingest_pipeline."""
```

The plugin returns raw `MemoryEntry` objects with `content` filled in. The scheduler passes them through `ingest_pipeline.ingest()` which handles summarisation, embedding, and storage. Plugins do not call Ollama directly.

### 2.2 Plugin loader

`brain/app/ingestion/plugins/__init__.py` implements the loader:

```python
def discover_plugins() -> list[PluginModule]:
    """Scan the plugins/ directory, import each module, run health_check().
    Return only plugins that pass. Log skipped plugins."""
```

Called once at startup (FastAPI lifespan). Returns a list of active plugin modules. The scheduler uses this list to register jobs.

**Discovery rules:**
- Any `.py` file in `plugins/` that is not `__init__.py` or a stub (`_stub.py` suffix) is treated as a plugin candidate
- If any `REQUIRED_ENV` value is empty/missing → skip without error
- If `health_check()` raises or returns False → skip, log warning
- Skipped plugins never affect brain startup

### 2.3 Scheduler

`brain/app/ingestion/scheduler.py` wraps APScheduler:

```python
def start_scheduler(active_plugins: list[PluginModule]) -> AsyncScheduler:
    """Create AsyncScheduler, register one job per active plugin, start it."""

async def run_plugin(plugin: PluginModule):
    """Called by scheduler on interval. Calls plugin.ingest(since=last_run),
    pipes each result through ingest_pipeline.ingest(), updates last_run timestamp."""
```

`last_run` per plugin is stored in SQLite (`plugin_state` table: `plugin_name`, `last_run`). On first run, `since = now() - schedule_interval` (i.e. pull the last full window immediately).

Wired into `main.py` lifespan:
```python
async with lifespan:
    init_db()
    active = discover_plugins()
    scheduler = start_scheduler(active)
    yield
    scheduler.shutdown()
```

### 2.4 Plugin status in `list_projects` MCP tool

`handle_list_projects()` in `mcp/tools.py` prepends plugin status:

```
Active plugins:   confluence ✅  pagerduty ✅
Inactive plugins: clickhouse ❌ (no credentials)

## Projects
...
```

The active/inactive lists are populated from the loader's last discovery result, stored in a module-level variable at startup.

---

## 3. Confluence Plugin

**File:** `brain/app/ingestion/plugins/confluence.py`

### Credentials
```bash
CONFLUENCE_URL=https://your-confluence.example.com/
CONFLUENCE_TOKEN=your-personal-access-token
```

### What it pulls
Pages where the authenticated user is the **author or last modifier**, updated since `last_run`. Uses Confluence REST API v1:

```
GET /rest/api/content/search
  ?cql=lastModified > "YYYY-MM-DD" AND contributor = currentUser()
  &expand=body.storage,version,space
  &limit=50
```

### Deduplication
Before ingesting a page, check if a memory with `source = page_url` exists in SQLite. If it does, compare the stored `timestamp` against the page's `last_modified`. Re-ingest only if the page is newer. This prevents duplicate entries accumulating across scheduler runs.

### What gets stored per page

```python
MemoryEntry(
    content=plain_text_body,   # HTML stripped to plain text (first 8000 chars)
    type="confluence",
    project=detect_project(CWD),  # "unknown" at scheduler time — use space key instead
    tags=[space_key, page_id],
    source=page_url,           # full Confluence URL — returned by get_memory()
)
```

`project` for plugin-ingested entries uses the Confluence space key lowercased (e.g. `"eze"`, `"dev"`) rather than a filesystem project slug, since the scheduler has no CWD context.

### Schedule
Every 6 hours.

### health_check
- `CONFLUENCE_URL` and `CONFLUENCE_TOKEN` are non-empty
- `GET {CONFLUENCE_URL}/rest/api/user/current` returns 200

---

## 4. PagerDuty Plugin

**File:** `brain/app/ingestion/plugins/pagerduty.py`

### Credentials
```bash
PAGERDUTY_TOKEN=your-pd-api-token
```

### What it pulls
Incidents **assigned to the current user or their teams**, resolved within the last 48h (relative to `last_run`). Uses PagerDuty REST API v2:

```
GET /incidents
  ?statuses[]=resolved
  &since=<last_run>
  &assigned_to_user=<current_user_id>
```

Current user ID is fetched once at `health_check()` time via `GET /users/me` and cached for the lifetime of the scheduler.

### What gets stored per incident (summary only)

```python
content = (
    f"[{severity}] {title} — {service_name} — "
    f"resolved in {duration_minutes}m ({resolved_at_formatted})"
)

MemoryEntry(
    content=content,
    type="pagerduty",
    project="pagerduty",         # fixed project slug for all PD incidents
    tags=[service_name, severity, incident_id],
    source=incident_html_url,    # link to the incident in PagerDuty web UI
    importance=4,                # incidents are always important; skip Ollama scoring
)
```

`summary` is set to the same string as `content` (it's already short — no Ollama summarisation needed). `importance` is hardcoded to 4 (important); Ollama scoring is skipped for incidents.

### Deduplication
Check `source = incident_html_url` in SQLite before inserting. Skip if already present.

### Schedule
Every 2 hours.

### health_check
- `PAGERDUTY_TOKEN` is non-empty
- `GET https://api.pagerduty.com/users/me` with `Authorization: Token token=<PD_TOKEN>` returns 200

---

## 5. Stub Plugins

**Files:** `brain/app/ingestion/plugins/clickhouse_stub.py`, `brain/app/ingestion/plugins/jira_stub.py`

Named with `_stub` suffix so the loader skips them automatically. Each file contains the contract interface with `raise NotImplementedError` bodies and a comment explaining what it would do when implemented. Provides a template for future plugins.

---

## 6. `brain` CLI

**File:** `cli/brain.py`
**Runtime:** Python 3 stdlib only — no pip installs
**Assumed install path:** `~/memorybrain/` (as documented in HOW_IT_WORKS.md). The CLI reads its own location via `__file__` to find `docker-compose.yml` and hooks — so it works from any clone path, not just `~/memorybrain/`.
**Installation:** `brain setup` adds a shell alias to `~/.bashrc` / `~/.zshrc`

### Commands

#### `brain setup [--auto-detect]`
Idempotent full setup. Safe to re-run on any machine at any time.

Steps (each is skipped if already done):
1. Check Docker is installed and running
2. If `--auto-detect`: read `~/.claude.json`, extract credentials for known MCP servers:
   - `mcp-atlassian` → `CONFLUENCE_URL` + `CONFLUENCE_TOKEN`
   - `pagerduty` MCP (if present) → `PAGERDUTY_TOKEN`
3. Write/update `.env` at `~/memorybrain/.env` — only overwrites keys that are missing or changed
4. `docker compose up -d` — skips if containers already running
5. Pull Ollama models (`nomic-embed-text`, `llama3.2:3b`) — skips if already present
6. `claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse` — skips if already registered (checks `claude mcp list` output)
7. Install hooks to `~/.claude/hooks/` — skips if hooks already point to memorybrain versions (checks file content hash)
8. Add `alias brain='python3 ~/memorybrain/cli/brain.py'` to `~/.bashrc` and `~/.zshrc` — skips if alias already present
9. Print summary: what was done, what was skipped, what needs manual config

**Output example:**
```
MemoryBrain setup
─────────────────────────────────────────
✅ Docker running
✅ .env updated (CONFLUENCE_URL, CONFLUENCE_TOKEN auto-detected)
⚠️  PAGERDUTY_TOKEN not found — add to .env manually
✅ Docker containers started
✅ Ollama models already present
✅ MCP server already registered
✅ Hooks installed
✅ Shell alias added (restart terminal or: source ~/.bashrc)

Brain is running at http://localhost:7741
Active plugins will start after next scheduler cycle (up to 6h).
```

#### `brain add "note text" [--project slug] [--tags tag1,tag2]`
POSTs to `POST /ingest/note`. If `--project` is not given, uses `.brainproject` in CWD or heuristic. Prints the assigned ID and generated summary.

#### `brain import <path> [--project slug]`
Reads a file and POSTs to `POST /ingest/file`. Accepts `.md`, `.txt`, `.json`. Prints ID + summary.

#### `brain seed [--project slug]`
Bulk import: finds all `MEMORY.md` and `HANDOVER-*.md` files in CWD and imports them. Used when first setting up MemoryBrain on a machine with existing MEMORY.md files. Prints count of entries imported.

#### `brain status`
Calls `GET /health` and `GET /status` (new structured endpoint — see below). Prints:
```
Brain:    ✅ running (http://localhost:7741)
Projects: 4
Plugins:  confluence ✅  pagerduty ✅  clickhouse ❌
```
If brain is not running, prints instructions to start it.

**New `GET /status` endpoint** added to `main.py`:
```python
@app.get("/status")
async def status():
    return {
        "project_count": len(list_projects()),
        "active_plugins": [p.MEMORY_TYPE for p in ACTIVE_PLUGINS],
        "inactive_plugins": [p.MEMORY_TYPE for p in INACTIVE_PLUGINS],
    }
```
`ACTIVE_PLUGINS` and `INACTIVE_PLUGINS` are module-level lists set by the loader at startup.

### Error handling
- Brain not running → print `Brain is not running. Start with: docker compose -f ~/memorybrain/docker-compose.yml up -d` and exit 1
- HTTP errors → print status code + response body, exit 1
- All other exceptions → print readable message, never a raw Python traceback

---

## 7. File Structure Changes

New files added to Part 1 structure:

```
brain/app/ingestion/
├── plugins/
│   ├── __init__.py          # loader: discover_plugins(), ACTIVE_PLUGINS global
│   ├── confluence.py        # Confluence plugin
│   ├── pagerduty.py         # PagerDuty plugin
│   ├── clickhouse_stub.py   # stub — not scheduled
│   └── jira_stub.py         # stub — not scheduled
└── scheduler.py             # APScheduler setup, run_plugin(), plugin_state table

cli/
└── brain.py                 # full CLI

brain/tests/
├── test_plugins.py          # plugin loader + health_check mocking
├── test_scheduler.py        # scheduler registration + last_run tracking
├── test_confluence_plugin.py
├── test_pagerduty_plugin.py
└── test_brain_cli.py        # CLI commands (subprocess or direct function calls)
```

Modified files:
- `brain/app/main.py` — lifespan updated to start/stop scheduler; add `GET /status` endpoint
- `brain/app/mcp/tools.py` — `handle_list_projects` updated with plugin status
- `brain/app/storage.py` — `plugin_state` table added to `init_db()`
- `brain/requirements.txt` — add `apscheduler>=3.10.4`
- `HOW_IT_WORKS.md` — Part 2 setup section added
- `PROGRESS_LOG.md` — updated

---

## 8. Decisions Locked In

| Decision | Choice | Reason |
|---|---|---|
| Scheduler location | APScheduler inside FastAPI | Simplest; single process; personal tool |
| Confluence scope | Author or last modifier only | Low noise; personally relevant |
| PagerDuty data | Summary only (title, service, duration) | Enough for recall; no sensitive alert data |
| CLI delivery | Python stdlib + shell alias | Works on Mac/Linux/WSL; no pip; ergonomic |
| `brain setup` | Idempotent full setup | Portable; Claude can run autonomously |
| Plugin discovery | File-based auto-discovery | Drop a file = add a plugin |
| Stub plugins | `_stub.py` suffix = auto-skipped | Template without activation risk |
| PagerDuty importance | Hardcoded 4 | Incidents are always important; skip Ollama |
| Plugin project slug | Space key (Confluence) / "pagerduty" (PD) | Scheduler has no CWD context |
| Deduplication | `source` URL + timestamp comparison | Prevents accumulation across scheduler runs |

---

## 9. Testing Strategy

- **Plugin loader:** mock `REQUIRED_ENV` presence/absence, mock `health_check()` pass/fail
- **Scheduler:** mock APScheduler, verify jobs registered only for active plugins, verify `last_run` persisted
- **Confluence plugin:** mock `httpx` responses, verify deduplication logic, verify plain-text stripping
- **PagerDuty plugin:** mock `httpx` responses, verify summary format, verify importance=4 set
- **CLI:** call handler functions directly (not subprocess), mock HTTP calls to brain
- All existing 38 tests must continue to pass
