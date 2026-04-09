# Plugin Removal + MCP Tool Awareness (v0.4.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove all hardcoded polling plugins from MemoryBrain, add a `GET /mcp-tools` endpoint that reads `~/.claude.json` to report registered MCP tools, and inject these into every session startup.

**Architecture:** FastAPI app loses its APScheduler-based plugin system entirely. A new `mcp_discovery.py` module reads `~/.claude.json` at request time and returns the `mcpServers` keys as a sorted list. The session hook gains a block that injects detected tools into session context on every startup. CLI `brain setup` loses credential-detection and gains a live MCP tools display at the end.

**Tech Stack:** Python, FastAPI, pytest, bash, git

---

## File Map

### Delete (12 files)
- `brain/app/ingestion/plugins/__init__.py`
- `brain/app/ingestion/plugins/confluence.py`
- `brain/app/ingestion/plugins/pagerduty.py`
- `brain/app/ingestion/plugins/clickhouse.py`
- `brain/app/ingestion/plugins/clickhouse_stub.py`
- `brain/app/ingestion/plugins/jira_stub.py`
- `brain/app/ingestion/scheduler.py`
- `brain/tests/test_clickhouse_plugin.py`
- `brain/tests/test_confluence_plugin.py`
- `brain/tests/test_pagerduty_plugin.py`
- `brain/tests/test_plugins.py`
- `brain/tests/test_scheduler.py`

### Create (2 files)
- `brain/app/mcp_discovery.py` — reads `~/.claude.json`, returns sorted MCP server names
- `brain/tests/test_mcp_discovery.py` — 6 tests: 5 unit + 1 endpoint

### Modify (9 files)
- `brain/app/main.py` — remove plugin imports/lifespan, add `/mcp-tools` endpoint, bump version to 0.4.0
- `brain/app/mcp/tools.py` — remove plugin imports + status header from `handle_list_projects`
- `brain/app/storage.py` — remove `plugin_state` table + 3 plugin-only functions
- `brain/app/auth.py` — add `/mcp-tools` to `PUBLIC_PATHS`
- `brain/requirements.txt` — remove `apscheduler`
- `.env.example` — remove plugin env vars
- `brain/tests/test_main.py` — remove plugin assertions from status test
- `hooks/session-ingest.sh` — add `/mcp-tools` injection block
- `cli/brain.py` — remove credential detection, add MCP tools display, fix `cmd_status`

---

## Task 1: TDD — mcp_discovery + /mcp-tools endpoint + decouple main.py

Write the tests first (RED), then create `mcp_discovery.py` (partial GREEN), then rewrite `main.py`/`mcp/tools.py` to add the endpoint and remove plugin imports (full GREEN). Plugin files are NOT deleted yet — that's Task 2.

**Files:**
- Create: `brain/tests/test_mcp_discovery.py`
- Create: `brain/app/mcp_discovery.py`
- Modify: `brain/app/main.py`
- Modify: `brain/app/mcp/tools.py`
- Modify: `brain/app/auth.py`
- Modify: `brain/tests/test_main.py`

- [ ] **Step 1: Write test_mcp_discovery.py**

Create `brain/tests/test_mcp_discovery.py`:

```python
import json
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.mcp_discovery import read_mcp_tools
from app.main import app

client = TestClient(app)


def test_reads_mcp_servers_sorted(tmp_path):
    config = {
        "mcpServers": {
            "pagerduty": {},
            "clickhouse-iom": {},
            "memorybrain": {},
            "confluence-mcp": {},
        }
    }
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert result["tools"] == ["clickhouse-iom", "confluence-mcp", "memorybrain", "pagerduty"]
    assert result["source"] == str(p)
    assert "error" not in result


def test_returns_empty_when_file_missing(tmp_path):
    result = read_mcp_tools(str(tmp_path / "nonexistent.json"))
    assert result["tools"] == []
    assert result["source"] is None
    assert "error" in result


def test_returns_empty_when_malformed_json(tmp_path):
    p = tmp_path / "claude.json"
    p.write_text("not valid json {{{")
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" in result


def test_memorybrain_appears_in_list(tmp_path):
    config = {"mcpServers": {"memorybrain": {}, "other-tool": {}}}
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert "memorybrain" in result["tools"]


def test_empty_when_no_mcp_servers_key(tmp_path):
    config = {"someOtherKey": "value"}
    p = tmp_path / "claude.json"
    p.write_text(json.dumps(config))
    result = read_mcp_tools(str(p))
    assert result["tools"] == []
    assert "error" not in result


def test_mcp_tools_endpoint_returns_200():
    fake_result = {"tools": ["clickhouse-iom", "memorybrain"], "source": "~/.claude.json"}
    with patch("app.main.read_mcp_tools", return_value=fake_result):
        resp = client.get("/mcp-tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tools"] == ["clickhouse-iom", "memorybrain"]
    assert "source" in data
```

- [ ] **Step 2: Run tests — confirm RED**

```bash
cd /path/to/MemoryBrain
docker compose exec brain pytest brain/tests/test_mcp_discovery.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.mcp_discovery'` — all 6 fail to collect.

- [ ] **Step 3: Create brain/app/mcp_discovery.py**

```python
import json
import os
from pathlib import Path


def read_mcp_tools(claude_json_path: str = "~/.claude.json") -> dict:
    path = Path(os.path.expanduser(claude_json_path))
    try:
        with open(path) as f:
            data = json.load(f)
        tools = sorted(data.get("mcpServers", {}).keys())
        return {"tools": tools, "source": str(claude_json_path)}
    except FileNotFoundError:
        return {"tools": [], "source": None, "error": f"{claude_json_path} not found"}
    except json.JSONDecodeError as e:
        return {"tools": [], "source": None, "error": f"Invalid JSON in {claude_json_path}: {e}"}
    except Exception as e:
        return {"tools": [], "source": None, "error": str(e)}
```

- [ ] **Step 4: Run — 5 unit tests green, endpoint test still red**

```bash
docker compose exec brain pytest brain/tests/test_mcp_discovery.py -v
```

Expected:
```
PASSED test_reads_mcp_servers_sorted
PASSED test_returns_empty_when_file_missing
PASSED test_returns_empty_when_malformed_json
PASSED test_memorybrain_appears_in_list
PASSED test_empty_when_no_mcp_servers_key
FAILED test_mcp_tools_endpoint_returns_200  ← 404, endpoint doesn't exist yet
```

- [ ] **Step 5: Update brain/tests/test_main.py — remove plugin assertions**

Replace the full content of `brain/tests/test_main.py` with:

```python
from fastapi.testclient import TestClient
from unittest.mock import patch
from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_status_endpoint_returns_structure(tmp_db):
    with patch("app.main.DB_PATH", tmp_db):
        with patch("app.storage.DB_PATH", tmp_db):
            resp = client.get("/status")
            assert resp.status_code == 200
            data = resp.json()
            assert "project_count" in data
            assert "version" in data
            assert "active_plugins" not in data
            assert "inactive_plugins" not in data
```

- [ ] **Step 6: Rewrite brain/app/main.py**

Replace the full content of `brain/app/main.py`:

```python
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .storage import init_db, list_projects, get_next_session_notes, DB_PATH
from .auth import require_api_key
from .mcp_discovery import read_mcp_tools

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Brain started")
    yield


app = FastAPI(title="MemoryBrain", version="0.4.0", lifespan=lifespan)
sse_transport = SseServerTransport("/messages/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    try:
        await require_api_key(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


app.include_router(session_router)
app.include_router(manual_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    return {
        "version": "0.4.0",
        "project_count": len(list_projects(db_path=DB_PATH)),
    }


@app.get("/mcp-tools")
async def mcp_tools():
    return read_mcp_tools()


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


@app.get("/next-session")
async def next_session(project: str = ""):
    if not project:
        return {"notes": ""}
    notes = get_next_session_notes(project, db_path=DB_PATH)
    return {"notes": notes}


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

- [ ] **Step 7: Update brain/app/mcp/tools.py — remove plugin imports and header**

Make two changes to `brain/app/mcp/tools.py`:

**a) Remove this import line:**
```python
from ..ingestion.plugins import ACTIVE_PLUGINS, INACTIVE_PLUGINS
```

**b) Replace `handle_list_projects` with this simplified version:**
```python
async def handle_list_projects() -> str:
    projects = storage_list_projects(db_path=DB_PATH)
    lines = ["## Projects\n"]
    for p in projects:
        lines.append(f"**{p.slug}** — {p.name}")
        if p.one_liner:
            lines.append(f"  {p.one_liner}")
        lines.append(f"  Last activity: {p.last_activity.strftime('%Y-%m-%d')}")
        lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 8: Add /mcp-tools to PUBLIC_PATHS in brain/app/auth.py**

Change:
```python
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json"}
```

To:
```python
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/mcp-tools"}
```

- [ ] **Step 9: Run all tests — confirm full GREEN**

```bash
docker compose exec brain pytest brain/tests/ -v
```

Expected: All tests PASS, including all 6 in `test_mcp_discovery.py`. Plugin test files still exist and still pass (plugin source files still exist — deleted in Task 2).

- [ ] **Step 10: Commit**

```bash
git add brain/tests/test_mcp_discovery.py \
        brain/app/mcp_discovery.py \
        brain/app/main.py \
        brain/app/mcp/tools.py \
        brain/app/auth.py \
        brain/tests/test_main.py
git commit -m "feat: add mcp_discovery module, GET /mcp-tools endpoint; decouple main.py from plugin system"
```

---

## Task 2: Delete plugin files and their tests

With `main.py` and `mcp/tools.py` no longer importing from plugins, it is safe to delete all plugin code.

**Files:**
- Delete (12): all plugin source + scheduler + plugin test files

- [ ] **Step 1: Delete all plugin source files and scheduler**

```bash
git rm brain/app/ingestion/plugins/__init__.py \
       brain/app/ingestion/plugins/confluence.py \
       brain/app/ingestion/plugins/pagerduty.py \
       brain/app/ingestion/plugins/clickhouse.py \
       brain/app/ingestion/plugins/clickhouse_stub.py \
       brain/app/ingestion/plugins/jira_stub.py \
       brain/app/ingestion/scheduler.py
```

- [ ] **Step 2: Delete plugin test files**

```bash
git rm brain/tests/test_clickhouse_plugin.py \
       brain/tests/test_confluence_plugin.py \
       brain/tests/test_pagerduty_plugin.py \
       brain/tests/test_plugins.py \
       brain/tests/test_scheduler.py
```

- [ ] **Step 3: Run remaining tests — confirm no regressions**

```bash
docker compose exec brain pytest brain/tests/ -v
```

Expected: All remaining tests PASS. No `ImportError` — `main.py` no longer imports from the deleted modules.

- [ ] **Step 4: Commit**

```bash
git commit -m "feat: remove plugin system (confluence, pagerduty, clickhouse, apscheduler)"
```

---

## Task 3: Clean up storage.py

Remove the `plugin_state` table and the three functions that exclusively supported plugins. Existing SQLite databases keep the `plugin_state` table harmlessly — no migration needed.

**Files:**
- Modify: `brain/app/storage.py`

- [ ] **Step 1: Remove plugin_state table creation from init_db()**

In `brain/app/storage.py`, remove these 6 lines from `init_db()`:

```python
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plugin_state (
                plugin_name TEXT PRIMARY KEY,
                last_run TEXT NOT NULL
            )
        """)
```

- [ ] **Step 2: Remove the three plugin-only functions**

Remove these three functions entirely from `brain/app/storage.py`:

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

- [ ] **Step 3: Run tests**

```bash
docker compose exec brain pytest brain/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 4: Commit**

```bash
git add brain/app/storage.py
git commit -m "refactor: remove plugin_state table and plugin-only storage functions"
```

---

## Task 4: Clean up requirements.txt and .env.example

**Files:**
- Modify: `brain/requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Remove apscheduler from brain/requirements.txt**

Replace the full content of `brain/requirements.txt` with:

```
fastapi~=0.135.0
uvicorn[standard]~=0.42.0
mcp~=1.26.0
chromadb~=1.5.0
ollama~=0.6.0
httpx~=0.28.0
pydantic~=2.12.0
# test
pytest~=9.0.0
pytest-asyncio~=1.3.0
```

Note: `httpx` stays — FastAPI's `TestClient` requires it internally.

- [ ] **Step 2: Simplify .env.example**

Replace the full content of `.env.example` with:

```
# CORE — required
BRAIN_PORT=7741
OLLAMA_URL=http://ollama:11434

# AUTHENTICATION (optional — if unset, all endpoints are open)
# Set to any random string to require X-Brain-Key header on all requests
BRAIN_API_KEY=
```

- [ ] **Step 3: Rebuild Docker image**

```bash
docker compose build brain
docker compose up -d brain
```

Expected: Build succeeds. `apscheduler` is no longer installed in the container.

- [ ] **Step 4: Run tests in fresh container**

```bash
docker compose exec brain pytest brain/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add brain/requirements.txt .env.example
git commit -m "chore: remove apscheduler dependency; simplify .env.example"
```

---

## Task 5: Update session-ingest.sh — inject MCP tools at session start

**Files:**
- Modify: `hooks/session-ingest.sh`

- [ ] **Step 1: Replace full content of hooks/session-ingest.sh**

```bash
#!/usr/bin/env bash
# MemoryBrain session-start hook
# Injects a compact project summary into session context on startup.
# Called by Claude Code session-start hook. CWD = project directory.

set -euo pipefail

BRAIN_URL="${MEMORYBRAIN_URL:-http://localhost:7741}"
CWD="${1:-$(pwd)}"

# Validate BRAIN_URL is localhost-only (prevent SSRF via env manipulation)
case "$BRAIN_URL" in
    http://localhost:*|http://127.0.0.1:*|http://\[::1\]:*) ;;
    *) echo "[memorybrain] BRAIN_URL must be localhost — refusing to connect to ${BRAIN_URL}" >&2; exit 0 ;;
esac

# Detect project slug: check for .brainproject file first, then heuristic
PROJECT_SLUG=""
if [ -f "${CWD}/.brainproject" ]; then
    PROJECT_SLUG=$(cat "${CWD}/.brainproject" | tr -d '[:space:]')
fi

# Build auth header if API key is set
CURL_AUTH_ARGS=()
if [ -n "${BRAIN_API_KEY:-}" ]; then
    CURL_AUTH_ARGS=(-H "X-Brain-Key: ${BRAIN_API_KEY}")
fi

# Check if brain is running
if ! curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/health" > /dev/null 2>&1; then
    # Brain not running — fall back to legacy MEMORY.md if present
    if [ -f "${CWD}/memory/MEMORY.md" ]; then
        echo "# Context (from MEMORY.md — MemoryBrain not running)"
        head -100 "${CWD}/memory/MEMORY.md"
    fi
    exit 0
fi

# Fetch startup summary
SUMMARY=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/startup-summary" | python3 -c "import sys,json; print(json.load(sys.stdin)['summary'])" 2>/dev/null || echo "")

if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY"
fi

# Inject next-session plan if a project is detected
if [ -n "$PROJECT_SLUG" ]; then
    NEXT_NOTES=$(curl -sf "${CURL_AUTH_ARGS[@]}" "${BRAIN_URL}/next-session?project=${PROJECT_SLUG}" | python3 -c "import sys,json; print(json.load(sys.stdin).get('notes',''))" 2>/dev/null || echo "")
    if [ -n "$NEXT_NOTES" ]; then
        echo ""
        echo "## Next Session Plan — ${PROJECT_SLUG}"
        echo "$NEXT_NOTES"
    fi
fi

# Inject available MCP tools (public endpoint — no auth header needed)
MCP_TOOLS=$(curl -sf "${BRAIN_URL}/mcp-tools" | python3 -c "
import sys, json
data = json.load(sys.stdin)
tools = data.get('tools', [])
if tools:
    print('## Available MCP Tools')
    for t in tools:
        print(f'- {t}')
    print()
    print('MemoryBrain will store what you retrieve with these tools.')
" 2>/dev/null || echo "")

if [ -n "$MCP_TOOLS" ]; then
    echo ""
    echo "$MCP_TOOLS"
fi
```

- [ ] **Step 2: Verify script is syntactically valid**

```bash
bash -n hooks/session-ingest.sh
```

Expected: No output (no syntax errors).

- [ ] **Step 3: Commit**

```bash
git add hooks/session-ingest.sh
git commit -m "feat: inject available MCP tools into session startup context"
```

---

## Task 6: Update cli/brain.py

Remove credential auto-detection from `cmd_setup`, add MCP tools display at end of setup, fix `cmd_status` to show version instead of plugins.

**Files:**
- Modify: `cli/brain.py`

- [ ] **Step 1: Replace full content of cli/brain.py**

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
import shutil
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

def _post(path: str, body: dict) -> dict:
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
    result = _post("/ingest/note", {
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
        print(f"  \u2705 {f.name} \u2192 {result['id']}")
    print(f"Done \u2014 {len(files)} files imported.")


def cmd_status():
    _get("/health")
    data = _get("/status")
    print(f"Brain:    \u2705 running ({BRAIN_URL})")
    print(f"Projects: {data.get('project_count', 0)}")
    print(f"Version:  {data.get('version', 'unknown')}")


def _run(cmd: list, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def cmd_setup(auto_detect: bool = False):
    print("MemoryBrain setup")
    print("\u2500" * 45)

    # 1. Docker running?
    r = _run(["docker", "ps"])
    if r.returncode != 0:
        print("\u274c Docker is not running. Start Docker Desktop / Rancher Desktop first.")
        sys.exit(1)
    print("\u2705 Docker running")

    # 2. Ensure .env exists
    env_path = MEMORYBRAIN_DIR / ".env"
    if not env_path.exists():
        example = MEMORYBRAIN_DIR / ".env.example"
        env_path.write_text(example.read_text() if example.exists() else "")
        print("\u2705 .env created from .env.example")
    else:
        print("\u23ed\ufe0f  .env \u2014 already exists")

    # 3. Start Docker containers
    compose_cmd = ["docker", "compose", "-f", str(MEMORYBRAIN_DIR / "docker-compose.yml")]
    ps = _run(compose_cmd + ["ps", "--status=running"])
    brain_running = "brain" in ps.stdout

    if not brain_running:
        _run(compose_cmd + ["up", "-d"], check=False)
        print("\u2705 Docker containers started")
    else:
        print("\u23ed\ufe0f  Docker containers \u2014 already running")

    # 4. Pull Ollama models
    models_out = _run(compose_cmd + ["exec", "ollama", "ollama", "list"]).stdout
    for model in ["embeddinggemma", "llama3.2:3b"]:
        if not any(line.startswith(model) for line in models_out.splitlines()):
            print(f"\u23f3 Pulling Ollama model: {model} (this may take a few minutes)...")
            _run(compose_cmd + ["exec", "ollama", "ollama", "pull", model])
            print(f"\u2705 {model} pulled")
        else:
            print(f"\u23ed\ufe0f  {model} \u2014 already present")

    # 5. Register MCP server with Claude Code
    mcp_list = _run(["claude", "mcp", "list"])
    if "memorybrain" not in mcp_list.stdout:
        _run(["claude", "mcp", "add", "-s", "user", "--transport", "sse",
              "memorybrain", f"{BRAIN_URL}/sse"])
        print("\u2705 MCP server registered")
    else:
        print("\u23ed\ufe0f  MCP server \u2014 already registered")

    # 6. Install hooks
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
            print(f"\u26a0\ufe0f  Hook source not found: {src}")
            continue
        if _file_hash(dst) != _file_hash(src):
            shutil.copy2(src, dst)
            dst.chmod(0o755)
            hooks_installed = True
    print("\u2705 Hooks installed" if hooks_installed else "\u23ed\ufe0f  Hooks \u2014 already up to date")

    # 7. Install Claude Code skills
    skills_src = MEMORYBRAIN_DIR / "skills"
    skills_dst = Path.home() / ".claude" / "skills"
    skills_installed = False
    if skills_src.exists():
        for skill_dir in skills_src.iterdir():
            if skill_dir.is_dir():
                skill_file = skill_dir / "SKILL.md"
                if skill_file.exists():
                    dst_skill_dir = skills_dst / skill_dir.name
                    dst_skill_dir.mkdir(parents=True, exist_ok=True)
                    dst_file = dst_skill_dir / "SKILL.md"
                    if _file_hash(dst_file) != _file_hash(skill_file):
                        shutil.copy2(skill_file, dst_file)
                        skills_installed = True
    print("\u2705 Skills installed" if skills_installed else "\u23ed\ufe0f  Skills \u2014 already up to date")

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
        print("\u2705 Shell alias added (run: source ~/.bashrc)")
    else:
        print("\u23ed\ufe0f  Shell alias \u2014 already present")

    # 9. Show detected MCP tools from ~/.claude.json via live endpoint
    print()
    try:
        with urllib.request.urlopen(f"{BRAIN_URL}/mcp-tools", timeout=5) as r:
            mcp_data = json.loads(r.read())
        tools = mcp_data.get("tools", [])
        if tools:
            print("Detected MCP servers in ~/.claude.json:")
            for t in tools:
                print(f"  \u2022 {t}")
            print()
            print("MemoryBrain will capture memories from whatever you retrieve with these tools.")
            print("No credentials needed \u2014 MemoryBrain is a passive store.")
        else:
            print("No MCP servers found in ~/.claude.json.")
            print("Add MCP servers to Claude Code and re-run setup to see them here.")
    except Exception:
        print("(Could not detect MCP tools \u2014 brain may still be starting up)")

    print()
    print(f"Brain is running at {BRAIN_URL}")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="brain", description="MemoryBrain CLI")
    sub = parser.add_subparsers(dest="command")

    # setup
    p_setup = sub.add_parser("setup", help="Idempotent full setup")
    p_setup.add_argument("--auto-detect", action="store_true",
                         help="Read ~/.claude.json to detect registered MCP tools")

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

Key changes from old version:
- Step 2 (auto-detect): replaced credential extraction with a simple `.env` existence check
- `--auto-detect` flag kept for backwards compatibility; MCP tools are always shown after setup
- Step 9 added: calls `/mcp-tools` and prints detected tools
- `cmd_status()`: removed `active_plugins`/`inactive_plugins`, shows `version` instead

- [ ] **Step 2: Run full test suite**

```bash
docker compose exec brain pytest brain/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add cli/brain.py
git commit -m "feat: remove plugin credential detection from brain setup; add MCP tools display; fix brain status"
```

---

## Task 7: Update docs and PROGRESS_LOG.md

**Files:**
- Modify: `HOW_IT_WORKS.md`
- Modify: `README.md`
- Modify: `PROGRESS_LOG.md`

- [ ] **Step 1: Read HOW_IT_WORKS.md then make these changes**

```bash
cat HOW_IT_WORKS.md
```

Make the following edits:

**a) Add a "Philosophy" section immediately after the title/intro paragraph:**

```markdown
## Philosophy

MemoryBrain is a **passive, tool-agnostic memory store**. It does not pull data from external systems on a schedule. Claude retrieves data using its MCP tools (Confluence, ClickHouse, PagerDuty, etc.) and saves what it finds useful via `add_memory`. On a new machine with different MCP tools, MemoryBrain works identically — the memories reflect actual usage, not a bulk dump.

MCP tool awareness (knowing which tools are registered) comes from reading `~/.claude.json` at session start, not from plugin credentials.
```

**b) Remove the entire "Plugins" section** — any section describing Confluence/PagerDuty/ClickHouse polling intervals, credential env vars (`CONFLUENCE_URL`, `PAGERDUTY_TOKEN`, `CLICKHOUSE_IOM_URL`, `CLICKHOUSE_TOKEN`), or APScheduler.

**c) In any component/container list**, remove APScheduler and httpx if listed as plugin-specific components.

**d) Replace any "Step 6: Enable plugins" setup content with:**

```markdown
### Step 6: Detect registered MCP tools

After setup, MemoryBrain reads `~/.claude.json` and reports which MCP servers are registered:

```
Detected MCP servers in ~/.claude.json:
  • clickhouse-iom
  • confluence-mcp
  • pagerduty
  • memorybrain

MemoryBrain will capture memories from whatever you retrieve with these tools.
No credentials needed — MemoryBrain is a passive store.
```

Every session startup automatically injects the current MCP tool list into context via `GET /mcp-tools`.
```

- [ ] **Step 2: Read README.md then make these changes**

```bash
cat README.md
```

**a) Remove any bullet points about plugin polling** (Confluence, PagerDuty, ClickHouse scheduled ingestion, APScheduler).

**b) Update the "how it works" summary to passive-store framing.** Find the summary paragraph and update it to something like:

```markdown
MemoryBrain is a **passive memory store**. Claude retrieves data using its own MCP tools (Confluence, ClickHouse, PagerDuty, etc.) and saves what it finds useful with `add_memory`. The session hook automatically injects registered MCP tools into every session start, so Claude knows what tools are available on this machine.
```

- [ ] **Step 3: Add Session 6 entry to PROGRESS_LOG.md**

Prepend this entry at the top of the sessions list (after the header but before Session 5):

```markdown
## Session 6 — 2026-04-09

**Goal:** Remove hardcoded polling plugins; add MCP tool awareness

**Changes:**
- Deleted 12 files: plugin loader, Confluence/PagerDuty/ClickHouse plugins, scheduler, and their tests
- Simplified `main.py`: removed plugin imports and APScheduler lifecycle; bumped version to 0.4.0
- Simplified `mcp/tools.py`: removed plugin status header from `list_projects`
- Cleaned `storage.py`: removed `plugin_state` table and 3 plugin-only functions
- Removed `apscheduler` from `requirements.txt`; simplified `.env.example`
- Created `brain/app/mcp_discovery.py`: reads `~/.claude.json` at call-time, returns sorted MCP server names
- Added `GET /mcp-tools` endpoint (public, no auth required)
- Updated `hooks/session-ingest.sh`: injects "## Available MCP Tools" block at session startup
- Updated `cli/brain.py`: removed credential auto-detection; `brain setup` shows detected MCP tools; `brain status` shows version
- Tagged v0.4.0

**Test count:** ~99 (down from 129 — removed ~35 plugin/scheduler tests, added 6 mcp_discovery tests)
```

- [ ] **Step 4: Commit docs**

```bash
git add HOW_IT_WORKS.md README.md PROGRESS_LOG.md
git commit -m "docs: update for v0.4.0 — passive store philosophy, remove plugin docs, add MCP awareness"
```

---

## Task 8: Tag v0.4.0 and push to GitHub

- [ ] **Step 1: Verify all tests pass**

```bash
docker compose exec brain pytest brain/tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 2: Note exact test count**

```bash
docker compose exec brain pytest brain/tests/ --co -q 2>/dev/null | tail -3
```

Update the Session 6 PROGRESS_LOG entry with the exact count if it differs from ~99, then commit the correction.

- [ ] **Step 3: Tag v0.4.0**

```bash
git tag v0.4.0
```

- [ ] **Step 4: Push master and tag to GitHub**

```bash
git push origin master
git push origin v0.4.0
```

Expected: GitHub shows new commits and `v0.4.0` tag.

- [ ] **Step 5: Update local memory file**

Update `/home/migueler/.claude/projects/-mnt-c-git--git/memory/project_memorybrain.md`:
- Set `Latest tag: v0.4.0`
- Set `Tests: ~99 passing` (use exact count from Step 2)
- Remove plugin items from "Architecture" (APScheduler, Confluence/PagerDuty/ClickHouse plugins)
- Add to architecture: `GET /mcp-tools` endpoint, session hook injects MCP tools
- Remove "Enable Confluence/PagerDuty/ClickHouse plugins" from optional future work
- Add: "Passive store — no credentials needed, MCP tools detected from ~/.claude.json"

---

## Self-Review

**Spec coverage check:**
- Section 1 (Plugin Removal): Tasks 1–4 ✅
- Section 2 (/mcp-tools endpoint): Task 1 ✅
- Section 3 (session hook + brain setup): Tasks 5–6 ✅
- Section 4 (docs + tag v0.4.0): Tasks 7–8 ✅

**Placeholder scan:** None found — every step has exact code or exact commands.

**Type consistency:** `read_mcp_tools` signature matches across test file (calls with `str(p)`) and implementation (accepts `str`, defaults to `"~/.claude.json"`). `/mcp-tools` route in `main.py` matches patch target `app.main.read_mcp_tools` in endpoint test. `PUBLIC_PATHS` addition in `auth.py` matches route path `/mcp-tools` exactly.
