# Design: Plugin Removal + MCP Tool Awareness (v0.4.0)

**Date:** 2026-04-09
**Status:** Approved
**Tag:** v0.4.0

---

## Problem

MemoryBrain was built with hardcoded plugins (Confluence, PagerDuty, ClickHouse) that
periodically poll external systems and store the results as memories. This is wrong for
three reasons:

1. **Redundant** — we already have MCP tools for Confluence, ClickHouse, and PagerDuty.
   Claude retrieves from those directly. A separate polling loop is duplicate infrastructure.

2. **Not portable** — on a new machine with different MCP tools, the hardcoded plugins
   either fail silently or require manual credential setup. The system is not tool-agnostic.

3. **Wrong direction of data flow** — the plugins pull bulk data regardless of what Claude
   actually found useful. MemoryBrain should store what Claude retrieved and deemed worth
   remembering, not a scheduled dump of everything.

## Philosophy (locked in)

MemoryBrain is a **passive, tool-agnostic memory store**. It does not pull from external
systems. Claude retrieves data using its MCP tools (Confluence, ClickHouse, PagerDuty, etc.)
and saves what it finds useful via `add_memory`. On a new machine with different MCP tools,
MemoryBrain works identically — the memories reflect actual usage.

MCP tool *awareness* (knowing what tools are registered) comes from reading `~/.claude.json`
at session start, not from plugin credentials.

---

## Section 1: Plugin Removal

### Files to delete

| File | Reason |
|---|---|
| `brain/app/ingestion/plugins/__init__.py` | Plugin loader |
| `brain/app/ingestion/plugins/confluence.py` | Hardcoded Confluence polling |
| `brain/app/ingestion/plugins/pagerduty.py` | Hardcoded PagerDuty polling |
| `brain/app/ingestion/plugins/clickhouse.py` | Hardcoded ClickHouse polling |
| `brain/app/ingestion/plugins/clickhouse_stub.py` | Dead stub |
| `brain/app/ingestion/plugins/jira_stub.py` | Dead stub |
| `brain/app/ingestion/scheduler.py` | APScheduler lifecycle |
| `brain/tests/test_clickhouse_plugin.py` | Plugin tests |
| `brain/tests/test_confluence_plugin.py` | Plugin tests |
| `brain/tests/test_pagerduty_plugin.py` | Plugin tests |
| `brain/tests/test_plugins.py` | Plugin loader tests |
| `brain/tests/test_scheduler.py` | Scheduler tests |

### Files to modify

**`brain/requirements.txt`**
- Remove `apscheduler` (scheduler)
- Remove `httpx` (only used by Confluence and PagerDuty plugins)

**`.env.example`**
- Remove: `CONFLUENCE_URL`, `CONFLUENCE_TOKEN`, `PAGERDUTY_TOKEN`, `CLICKHOUSE_IOM_URL`, `CLICKHOUSE_TOKEN`
- Keep: `BRAIN_PORT`, `OLLAMA_URL`, `BRAIN_API_KEY`

**`brain/app/main.py`**
- Remove imports: `discover_plugins`, `ACTIVE_PLUGINS`, `INACTIVE_PLUGINS`, `start_scheduler`
- Remove from `lifespan()`: `discover_plugins()` call, `start_scheduler()`, `scheduler.shutdown()`
- Simplify `lifespan()` to: `init_db()`, log startup, yield
- Simplify `GET /status`: remove `active_plugins` and `inactive_plugins` fields

**`brain/app/mcp/tools.py`**
- Remove import: `ACTIVE_PLUGINS`, `INACTIVE_PLUGINS`
- Remove plugin status header block from `handle_list_projects()`

**`brain/app/storage.py`**
- Remove `plugin_state` table creation from `init_db()`
- Remove functions: `get_last_run()`, `set_last_run()`, `get_memory_by_source()`
- Note: existing DBs keep the `plugin_state` table harmlessly — no migration needed

---

## Section 2: `/mcp-tools` Endpoint

### New file: `brain/app/mcp_discovery.py`

Single function:

```python
def read_mcp_tools(claude_json_path: str = "~/.claude.json") -> dict:
    ...
```

- Expands `~`, reads the file, parses JSON
- Returns keys of the top-level `mcpServers` object as a sorted list
- Graceful on missing file or bad JSON — returns empty list with `error` field, never raises

Return shape:
```json
// success
{"tools": ["clickhouse-iom", "confluence-mcp", "memorybrain", "pagerduty"], "source": "~/.claude.json"}

// file missing or unreadable
{"tools": [], "source": null, "error": "~/.claude.json not found"}
```

`memorybrain` itself will appear in the list — this is expected and not filtered out.

### New endpoint in `main.py`

```
GET /mcp-tools
```

Calls `read_mcp_tools()`, returns the dict directly. Always public (no auth required,
same as `/health`) — local machine metadata only, no secrets.

### New tests: `brain/tests/test_mcp_discovery.py` (~5 tests)

- Valid `~/.claude.json` with `mcpServers` → correct names returned, sorted
- File missing → empty list, `error` field present, no exception
- Malformed JSON → empty list, `error` field present, no exception
- `memorybrain` appears in list (not filtered)
- `GET /mcp-tools` endpoint returns 200 with `{"tools": [...], "source": ...}` shape

---

## Section 3: Session Hook + `brain setup` Updates

### `hooks/session-ingest.sh`

After the existing startup summary block, add a second call to `GET /mcp-tools`.

Injected into session context when tools are found:

```
## Available MCP Tools
- clickhouse-iom
- confluence-mcp
- pagerduty
- memorybrain

MemoryBrain will store what you retrieve with these tools.
```

If `/mcp-tools` returns an empty list, errors, or the Brain is not running, this block
is silently skipped — same behaviour as the existing startup summary call.

### `cli/brain.py` — `brain setup --auto-detect`

**Remove:** all steps that detected and pre-filled plugin credentials into `.env`
(`CONFLUENCE_URL`, `CONFLUENCE_TOKEN`, `PAGERDUTY_TOKEN`, `CLICKHOUSE_IOM_URL`,
`CLICKHOUSE_TOKEN`).

**Add:** after Docker is up and MCP is registered, call `GET /mcp-tools` and print:

```
Detected MCP servers in ~/.claude.json:
  • clickhouse-iom
  • confluence-mcp
  • pagerduty
  • memorybrain

MemoryBrain will capture memories from whatever you retrieve with these tools.
No credentials needed — MemoryBrain is a passive store.
```

If `~/.claude.json` is missing or `mcpServers` is empty, print a neutral note and continue
without error.

---

## Section 4: Docs + Tag v0.4.0

### `HOW_IT_WORKS.md`
- Add "Philosophy" section at the top (passive store, tool-agnostic)
- Remove entire "Plugins" section (polling, scheduler, plugin env vars)
- Update container component list: remove APScheduler, httpx
- Replace setup Step 6 ("enable plugins") with MCP tools detection description

### `README.md`
- Remove plugin references from architecture list
- Update "how it works" summary to passive-store framing

### `PROGRESS_LOG.md`
- Add Session 6 entry

### Tag v0.4.0
Covers:
- Session 5 work: next-session feature, named volume fix, skills portability
- Session 6 work: plugin removal, MCP tool awareness

---

## Test count impact

| Change | Tests |
|---|---|
| Remove plugin/scheduler tests | ~−35 (5 test files) |
| Add `test_mcp_discovery.py` | +5 |
| **Net** | **~−30** |

Final count: ~99 tests (from 129). Exact count confirmed during implementation.
All remaining tests are for code that still exists.
