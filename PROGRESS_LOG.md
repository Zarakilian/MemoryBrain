# MemoryBrain — Progress Log

**READ THIS FIRST at the start of every session.**

---

## Status: v0.4.0 — FULLY OPERATIONAL ✅

**GitHub:** https://github.com/Zarakilian/MemoryBrain
**Latest tag:** `v0.4.0` (Session 9 changes committed — not re-tagged, no version bump)
**Tests:** 109 passing
**Docker:** Running (named volume), healthy, models pulled, /readiness endpoint live
**MCP registered:** `http://localhost:7741/sse`
**Hooks installed:** session-start + pre-compact
**Skills installed:** `log-everything` (/log-everything in Claude)
**Next action:** None — fully operational. Use `/log-everything` to save sessions.

---

## IMMEDIATE NEXT STEP

None. MemoryBrain is registered and tested. In any Claude session:
- Session startup auto-injects Brain summary + next-session notes
- `/log-everything` saves session to Brain and captures next-session plan
- `search_memory`, `add_memory`, `list_projects`, `get_recent_context` available as MCP tools

**Possible future work:**
- Add new memory types or search capabilities

---

## Part 2 — What needs to be built (10 tasks)

| Task | Component | Status |
|---|---|---|
| 1 | Storage additions — plugin_state table, get_memory_by_source, get/set_last_run | ✅ |
| 2 | Plugin loader — discover_plugins(), ACTIVE/INACTIVE_PLUGINS globals | ✅ |
| 3 | Scheduler — APScheduler, start_scheduler(), run_plugin() | ✅ |
| 4 | Wire into main.py — lifespan starts scheduler, GET /status endpoint | ✅ |
| 5 | Update list_projects MCP tool — plugin status preamble | ✅ |
| 6 | Confluence plugin — 6h schedule, deduplication, HTML strip | ✅ |
| 7 | PagerDuty plugin — 2h schedule, summary=content, importance=4 | ✅ |
| 8 | Stub plugins — clickhouse_stub.py + jira_stub.py | ✅ |
| 9 | brain CLI — setup (idempotent), add, import, seed, status + alias | ✅ |
| 10 | Docs + tag v0.2.0 — HOW_IT_WORKS + PROGRESS_LOG + push | ✅ |

---

## Key design decisions locked in (do not re-discuss)

- **Scheduler:** APScheduler (AsyncIOScheduler) inside FastAPI process
- **Confluence scope:** Pages where current user is author or last modifier
- **PagerDuty:** Summary only (title, service, duration) — importance hardcoded to 4, summary=content (no Ollama)
- **CLI delivery:** Python stdlib, cli/brain.py, installs shell alias
- **brain setup:** Idempotent — reads ~/.claude.json, pre-fills .env, starts Docker, pulls models, registers MCP, installs hooks, adds alias
- **Plugin discovery:** File-based, _stub.py suffix auto-skipped
- **Deduplication:** By source URL (both plugins)

---

## Part 2 key files (being created)

```
brain/app/ingestion/plugins/__init__.py   — loader
brain/app/ingestion/plugins/confluence.py
brain/app/ingestion/plugins/pagerduty.py
brain/app/ingestion/plugins/clickhouse_stub.py
brain/app/ingestion/plugins/jira_stub.py
brain/app/ingestion/scheduler.py
cli/brain.py
```

Modified:
```
brain/app/storage.py      — plugin_state table + 3 new functions
brain/app/main.py         — scheduler in lifespan + /status endpoint
brain/app/mcp/tools.py    — list_projects with plugin status
brain/requirements.txt    — add apscheduler>=3.10.4
HOW_IT_WORKS.md           — Part 2 section
```

---

## Part 1 — What was built (complete ✅)

| File | Purpose |
|---|---|
| `brain/app/main.py` | FastAPI entrypoint — lifespan (init_db), routes, SSE mount |
| `brain/app/models.py` | `MemoryEntry` + `Project` dataclasses |
| `brain/app/storage.py` | SQLite FTS5 — CRUD, keyword search, project tracking |
| `brain/app/chroma.py` | ChromaDB wrapper — semantic add/search/delete |
| `brain/app/summarise.py` | Ollama AsyncClient — embed, summarise, score_importance |
| `brain/app/ingest_pipeline.py` | Orchestrates: summarise → embed → SQLite + ChromaDB |
| `brain/app/search.py` | Hybrid RRF: FTS5 keyword + ChromaDB semantic → merged top-N |
| `brain/app/mcp/tools.py` | MCP Server + 6 tool handlers |
| `brain/app/ingestion/session.py` | POST /ingest/session |
| `brain/app/ingestion/manual.py` | POST /ingest/note, POST /ingest/file |
| `hooks/session-ingest.sh` | Claude Code session-start hook |
| `hooks/pre-compact-ingest.py` | Claude Code pre-compact hook |
| `docker-compose.yml` | brain + ollama services |

### Known debt (non-blocking, address later)
- ~~`datetime.utcnow()` deprecation warnings~~ → FIXED 2026-04-08
- ~~`score_importance` always overwrites caller-supplied importance~~ → FIXED 2026-04-08
- No file size limit on `/ingest/file` endpoint — already had 1MB cap since Part 1

---

## Session log

### 2026-04-09 — Session 9: Full subsystem readiness check

**Goal:** Make MemoryBrain "all go and ready" aware — session startup should check every subsystem (not just "is FastAPI alive?") and report degraded states with actionable fix instructions.

| Item | Status |
|---|---|
| `GET /readiness` endpoint — checks SQLite, ChromaDB, Ollama, both models | ✅ |
| `auth.py` — `/readiness` added to `PUBLIC_PATHS` (no API key needed) | ✅ |
| Session hook — readiness check section between version check and startup summary | ✅ |
| Degraded report: `## MemoryBrain — PARTIAL SERVICE` with ✗ per failed check | ✅ |
| Degraded report: explains what's available vs unavailable + exact fix commands | ✅ |
| Silent on healthy system — no output when all checks pass | ✅ |
| 5 new tests (109 total): all_ok, ollama_down, models_missing, chroma_down, public_no_auth | ✅ |
| Hook copied to `~/.claude/hooks/session-start-memory.sh` | ✅ |
| `HOW_IT_WORKS.md` updated — readiness step + degraded service modes table | ✅ |

**Docker Compose idempotency clarified:** `docker compose up -d` is always safe — no-op if running+unchanged, restarts if stopped, recreates if image changed. No "new container name" ever needed. Named volumes survive any container recreation.

**Degraded service modes documented:**
- Ollama down/models missing → read + keyword search still works; `add_memory` and semantic search unavailable
- ChromaDB down → read + keyword search + `add_memory` still work; semantic search unavailable
- SQLite down → nothing works

---

### 2026-04-09 — Session 8: Session startup health + version checks

**Goal:** Make MemoryBrain seamless — session startup should clearly report problems and tell the user exactly how to fix them.

| Item | Status |
|---|---|
| Add `VERSION` file to repo root (`0.4.0`) | ✅ |
| Session hook: "NOT RUNNING" message with exact `docker compose up -d` command | ✅ |
| Session hook: version mismatch check — compares `$MEMORYBRAIN_DIR/VERSION` vs `/status` | ✅ |
| Session hook: version mismatch shows exact rebuild command `docker compose up -d --build` | ✅ |
| `brain setup` now exports `MEMORYBRAIN_DIR=` alongside the shell alias | ✅ |
| `MEMORYBRAIN_DIR` added to `~/.bashrc` on this machine | ✅ |
| Hook copied to `~/.claude/hooks/session-start-memory.sh` | ✅ |
| `HOW_IT_WORKS.md` updated — startup flow + MCP discovery rationale | ✅ |

**Three tested scenarios:**
1. Brain running, versions match → normal startup summary, no warnings
2. Brain not running → clear message + exact start command using `$MEMORYBRAIN_DIR`
3. Brain running but outdated → shows running vs repo version + exact rebuild command

**MCP tools explanation (documented):** The `/mcp-tools` Docker endpoint was removed because the Docker `brain` user's home is `/app`, not the host home — so `~/.claude.json` was never found and the endpoint always returned empty. The feature is intact: the session hook reads `~/.claude.json` directly on the host (where it runs) and injects `## Available MCP Tools`. `mcp_discovery.py` module is kept and tested.

---

### 2026-04-09 — Session 7: Fix MCP discovery architecture (host-side, not Docker)

**Problem found during v0.4.0 testing:**
The `GET /mcp-tools` endpoint ran inside Docker. `~/.claude.json` is not mounted into the container (it contains credentials), so the endpoint always returned an empty tools list. The session hook and CLI both called this endpoint, meaning MCP tool awareness never worked.

**Root cause:** MCP discovery was incorrectly routed through Docker. The container's `brain` user home is `/app`, not the host's `~`. Mounting `~/.claude.json` into Docker would be a security risk.

**Fix: moved all MCP discovery to host-side execution.**

| Item | Status |
|---|---|
| Removed `GET /mcp-tools` Docker endpoint from `main.py` | ✅ |
| Removed endpoint import from `main.py` (`from .mcp_discovery import read_mcp_tools`) | ✅ |
| `hooks/session-ingest.sh` — replaced Docker HTTP call with direct `python3` read of `~/.claude.json` on host | ✅ |
| `cli/brain.py` `cmd_setup()` — replaced HTTP call with direct `Path.home() / ".claude.json"` read | ✅ |
| `brain/tests/test_mcp_discovery.py` — removed endpoint test; kept 7 unit tests for `read_mcp_tools()` directly | ✅ |
| `brain/tests/test_auth.py` — repurposed `test_mcp_tools_always_public` → `test_health_always_public` | ✅ |
| `HOW_IT_WORKS.md` — updated philosophy + MCP Tool Awareness section | ✅ |
| Hook copied to `~/.claude/hooks/session-start-memory.sh` | ✅ |

**Test count:** 104 (was 106; removed 1 endpoint test from test_mcp_discovery.py, repurposed 1 auth test — net -2)

**Verified working:**
- `bash ~/.claude/hooks/session-start-memory.sh /mnt/c/git/_git/MemoryBrain` → outputs Brain startup summary + `## Available MCP Tools` (9 servers listed)
- `brain setup --auto-detect` → correctly lists all 9 MCP servers from `~/.claude.json`

**Security principle:** `~/.claude.json` contains PAT tokens and API keys. It must never be mounted into Docker containers. Host-side reads (session hook + CLI) have legitimate access without any security risk.

---

### 2026-04-09 — Session 6: Plugin removal + MCP tool awareness (v0.4.0)

**What happened:**

| Item | Status |
|---|---|
| Removed plugin system (Confluence, PagerDuty, ClickHouse, APScheduler) | ✅ |
| Removed 5 plugin test files + scheduler test | ✅ |
| Removed plugin storage functions (`plugin_state` table, `get_last_run`, `set_last_run`, `get_memory_by_source`) | ✅ |
| Removed `apscheduler` from requirements.txt | ✅ |
| Removed plugin credentials from `.env.example` | ✅ |
| Added `brain/app/mcp_discovery.py` — reads `~/.claude.json`, returns sorted MCP server names | ✅ |
| Added `GET /mcp-tools` endpoint — always public, no auth required | ✅ |
| Added `brain/tests/test_mcp_discovery.py` — 6 tests | ✅ |
| Updated `hooks/session-ingest.sh` — injects `## Available MCP Tools` block at session start | ✅ |
| Updated `cli/brain.py` — removed credential auto-detection; added MCP tools display in setup | ✅ |
| Updated docs (HOW_IT_WORKS.md, README.md, PROGRESS_LOG.md) | ✅ |

**Test count:** 106 (was 129; removed ~35 plugin/scheduler tests, added 6 MCP discovery + 4 edge case + 1 plugin-era rejection tests; also fixed `test_importance_preserve.py` using removed `pagerduty` type)

**Philosophy locked in:** MemoryBrain is a passive, tool-agnostic store. No polling, no credentials for external services, no scheduled jobs. Claude retrieves with its MCP tools; MemoryBrain stores what Claude saves.

### 2026-04-09 — Session 5: Data persistence fix + portability + registration

**What happened:**

| Item | Status |
|---|---|
| Root cause of "empty DB after up -d" | ✅ Found: Rancher Desktop translates bind mounts to UUID virtiofsd paths; new container = new UUID = empty dir |
| Fix: named Docker volume (`memorybrain_brain_data`) | ✅ Applied — data now persists through any container recreate |
| MCP registered | ✅ `claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse` |
| Hooks installed | ✅ session-ingest.sh + pre-compact-ingest.py active |
| Next-session plan feature committed | ✅ storage.py, main.py, hooks |
| Skills: add to repo | ✅ `skills/log-everything/SKILL.md` added; brain setup now installs skills |
| HOW_IT_WORKS.md major update | ✅ model names, data location, restart behavior, skills, Option A/B setup |
| README quick start updated | ✅ one-command setup via brain CLI |
| Pushed to GitHub | ✅ 3 new commits on master |

**MCP session break behavior (documented):** After any container restart mid-session, SSE connection drops and MCP tool calls fail. Open a new Claude session to restore. This is expected — not a bug.

**Key finding on Rancher Desktop + WSL:**
- Bind mounts from WSL paths → Rancher translates to `/mnt/wsl/rancher-desktop/run/docker-mounts/<UUID>/`
- UUID is per-container (not per-volume), so `up -d` (recreate) = new UUID = empty dir
- Named volumes use `/var/lib/docker/volumes/<name>/_data` — stable across recreates

### 2026-04-08 — Session 4: Security Hardening (Opus 4.6 deep-dive)

**What happened:**
Full code audit + security hardening session. Verified all findings from pre-Part2 audit, discovered 6 new findings (N1-N6), implemented 7 fixes:

| Fix | Finding | What changed |
|-----|---------|--------------|
| **H4** | Content deduplication | SHA-256 hash of content\|project stored in `content_hash` column. `/ingest/note` and `/ingest/session` return 200 + `duplicate:true` on match. 8 new tests. |
| **H1** | MCP arg validation | `call_tool` now validates required/optional keys per tool, strips unknown keys, clamps `limit` to 1-100 and `days` to 1-365. 7 new tests. |
| **A1** | API key auth | New `auth.py` middleware. `BRAIN_API_KEY` env var — if set, requires `X-Brain-Key` header. `/health` + SSE always public. 5 new tests. |
| **N1** | Importance overwrite | `ingest_pipeline.py` now only calls `score_importance()` when `importance == 3` (default). PagerDuty's hardcoded 4 is preserved. 2 new tests. |
| **M1-M5** | Input validation | New `validate_entry()` in models.py: type enum, importance 1-5 clamp, project slug regex, tags bounds (20 items / 100 chars), content length 100K cap. Called at pipeline entry. 13 new tests. |
| **M7** | FTS5 triggers | Added `memories_ad` (AFTER DELETE) and `memories_au` (AFTER UPDATE) triggers. FTS5 index now stays in sync with all mutations. 3 new tests. |
| **L3** | datetime deprecation | All `datetime.utcnow()` replaced with `datetime.now(timezone.utc)`. New `utcnow()` helper in models.py. Zero deprecation warnings. |

**Files created:** `auth.py`, 5 test files
**Files modified:** `main.py`, `models.py`, `storage.py`, `ingest_pipeline.py`, `mcp/tools.py`, `ingestion/manual.py`, `ingestion/session.py`, `ingestion/scheduler.py`, `ingestion/plugins/confluence.py`, `.env.example`, `test_ingestion_endpoints.py`, `test_scheduler.py`

**Continuation — Part 3: ClickHouse APM plugin:**

| Item | Details |
|------|---------|
| `clickhouse.py` | Real plugin replacing stub. Queries `apm.otel_traces_local` every 12h for error rate + P95 by (service, operator). Importance 2-5 from error rate. 7 new tests. |
| `HOW_IT_WORKS.md` | New Security section added. |
| Hook auth | Both hooks now forward `BRAIN_API_KEY` as `X-Brain-Key` header automatically. |
| Tags | v0.3.0 (security) + v0.3.1 (ClickHouse plugin) pushed to GitHub. |

**Continuation — all remaining security items:**

| Fix | Finding | What changed |
|-----|---------|--------------|
| **H2** | OLLAMA_URL SSRF | New `validate_ollama_url()` in summarise.py — rejects non-http(s) schemes at module load. 8 new tests. |
| **H3** | BRAIN_URL SSRF | Both hooks (session-ingest.sh, pre-compact-ingest.py) now validate BRAIN_URL is localhost-only before connecting. |
| **L6** | Rate limiting | `asyncio.Semaphore(3)` wraps all ingest calls — max 3 concurrent pipeline runs. 2 new tests. |
| **L8** | Docker root | Dockerfile now creates `brain` user/group, `chown`s /app, runs as USER brain. |
| **A5** | Dead chroma_id | Removed from MemoryEntry dataclass and storage read/write. Column left in schema for backward compat. |
| **A6** | Cross-store txn | If ChromaDB write fails, SQLite entry is rolled back via `delete_memory()`. 2 new tests. |
| **L2** | Requirements pin | All deps pinned with `~=` (compatible release) to current installed versions. |

**Continuation — Docker deployment + models:**

| Item | Details |
|------|---------|
| Dockerfile fix | `gosu` entrypoint — chowns `/app/data` at runtime, then execs as `brain` user (UID 999). Static `USER` directive didn't work with mounted volumes. |
| docker-compose.yml | Mount `/etc/ssl/certs:ro` into Ollama container — fixes TLS cert verification through corporate proxy. |
| Ollama models | `embeddinggemma` (621MB) + `llama3.2:3b` (2GB) pulled successfully. |
| End-to-end test | Ingest → summary + importance scored → dedup works → startup summary works. All verified. |
| Tags | v0.3.2 pushed (includes Docker fixes). |

**Total new tests this session:** 57 (50 security/validation + 7 ClickHouse plugin)
**Final test count:** 129 passing, 0 warnings
**Service status:** Running, healthy, fully operational

### 2026-03-27 — Session 2: Part 2 designed + planned (session ended before execution)

**What happened:**
- HOW_IT_WORKS.md created — full architecture + portable setup guide (8 steps)
- PROGRESS_LOG.md created
- Part 2 brainstormed (4 questions answered: scheduler location A, Confluence scope A, PagerDuty detail A, CLI delivery C)
- Part 2 design spec written: `docs/superpowers/specs/2026-03-27-memorybrain-part2-design.md`
- Part 2 implementation plan written: `docs/superpowers/plans/2026-03-27-memorybrain-part2.md`
- Session ended before execution — plan is ready to run

### 2026-03-27 — Session 1: Part 1 built (subagent-driven, 10 tasks)

**What happened:**
- Design doc recovered from Claude file history, saved to `docs/design.md`
- Implementation plan written: `docs/superpowers/plans/2026-03-27-memorybrain-core.md`
- All 10 tasks implemented via subagent-driven development with spec + quality review per task
- Critical bug fixed: `async def` calling sync ollama client → switched to `ollama.AsyncClient`
- Production bug fixed: missing `init_db()` at startup → added FastAPI lifespan hook
- Pushed to GitHub as v0.1.0

---

## Key facts

- Port: `7741` (configurable via `BRAIN_PORT` in `.env`)
- DB: `./data/brain.db` (SQLite, Docker volume)
- Vectors: `./data/chroma/` (Docker volume)
- Ollama models: `embeddinggemma` (~200MB, #1 MTEB sub-500M) + `llama3.2:3b` (~2GB)
- Project slug: `.brainproject` file → fallback to last CWD segment
- This repo's slug: `memorybrain`
- MCP SSE URL: `http://localhost:7741/sse`

**To install Part 1 on a new machine (before Part 2 CLI exists):**
```bash
cd /mnt/c/git/_git/MemoryBrain   # or: git clone https://github.com/Zarakilian/MemoryBrain
cp .env.example .env
docker compose up -d
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
cp hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
```

## v0.5.0 — 2026-04-23

### Shipped
- Semantic supersession engine: auto-archives stale memories on ingest (type-aware thresholds)
- `add_memory` response includes `superseded` + `potential_supersessions`
- `search_memory` gains `include_history`, `tags`, `type_filter` params
- Recency decay on RRF scores (`RECENCY_DECAY_RATE` env var)
- `delete_memory` MCP tool (hard delete)
- `get_startup_summary` includes per-project recent state
- `description` param on `add_memory` bypasses LLM summariser
- Provider abstraction: Gemini and OpenAI-compatible backends alongside Ollama
- `brain update` CLI command — one-command upgrade on any machine
- Versioned migration system (`brain/app/migrations/`) — schema evolution without manual SQL
- `build_where()` helper for ChromaDB 1.5.x multi-key where syntax

### Known followup (v0.5.1)
- Migrate `GeminiProvider` from deprecated `google-generativeai` to `google-genai` package
  (all support for `google-generativeai` has ended upstream)
