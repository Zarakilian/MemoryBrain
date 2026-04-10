# MemoryBrain — Design Spec
**Date:** 2026-03-26
**Author:** migueler + Claude
**Status:** Approved — ready for implementation planning

---

## 1. Problem Statement

Every new Claude Code session starts with zero context. The user must manually provide
background via MEMORY.md files, handover docs, and long context dumps — which burns tokens,
wastes time, and breaks flow. The existing flat-file memory system (MEMORY.md + pre-compact
hooks) hits a 200-line truncation limit and has no search capability.

**Goal:** A persistent, searchable memory service that gives Claude automatic context on
every new session, remembers everything across projects, and pulls in external sources
(Confluence, PagerDuty, etc.) automatically — without bloating the context window.

---

## 2. Architecture Overview

```
~/memorybrain/
└── docker-compose.yml
    ├── brain container       FastAPI + SQLite FTS5 + ChromaDB + APScheduler
    └── ollama container      nomic-embed-text (embeddings) + llama3.2:3b (summarisation)

MCP transport: SSE on localhost:7741
Claude config: ~/.claude.json → memorybrain MCP entry
Session hooks: session-ingest.sh + pre-compact-ingest.py (replace existing hooks)
```

### How a new Claude session works (hybrid mode)

1. Session starts → `session-ingest.sh` hook calls `GET /startup-summary`
2. Brain returns compact ~150-token project index (active projects + last activity)
3. Hook injects summary into session context — Claude is immediately oriented
4. During work, Claude calls `search_memory("topic")` via MCP for deeper recall
5. Full document content only fetched when Claude explicitly calls `get_memory(id)`

### Setup on any new machine

```bash
git clone git@github.com:you/memorybrain ~/memorybrain
cd ~/memorybrain
cp .env.example .env           # fill in tokens for this machine's available services
brain setup --auto-detect      # optionally auto-reads ~/.claude.json to pre-fill .env
docker compose up -d
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
cp hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
```

Data is machine-local. Work PC and personal PC maintain completely independent databases.
The application (code + config) is portable; the data is not.

---

## 3. Data Model

### Memory entry — core unit

| Field        | Type         | Description |
|---|---|---|
| `id`         | UUID         | Unique identifier |
| `content`    | TEXT         | Full original content |
| `summary`    | TEXT         | 2–3 sentence AI-generated summary (returned by default in search) |
| `type`       | ENUM         | `session` `handover` `note` `confluence` `pagerduty` `clickhouse` `fact` `file` |
| `project`    | TEXT         | Project slug e.g. `monitoring`, `service-broker`, `personal` |
| `tags`       | JSON         | Array of strings e.g. `["alerting", "clickhouse", "grafana"]` |
| `source`     | TEXT         | File path or URL of origin |
| `importance` | INT 1–5      | Auto-scored by Ollama on ingestion; overridable manually |
| `timestamp`  | DATETIME     | When the memory was created |
| `chroma_id`  | TEXT         | Reference to the corresponding ChromaDB vector |

### Projects table

| Field           | Description |
|---|---|
| `slug`          | Machine-friendly ID e.g. `monitoring` |
| `name`          | Human-readable name |
| `last_activity` | Timestamp of most recent memory entry |
| `one_liner`     | Auto-maintained 1-sentence description |

The startup summary is this table serialised to ~150 tokens — the only thing auto-injected every session.

### Storage split

- **SQLite FTS5** (`data/brain.db`) — all structured data, full content, keyword search with BM25 ranking. Single file, zero maintenance.
- **ChromaDB** (`data/chroma/`) — vector store. Holds embeddings + `memory_id` reference only. Used for semantic search.
- The application layer presents a single unified abstraction over both.

---

## 4. Compression & Token Efficiency

Long documents are summarised on ingestion, never in the fast path:

```
Ingestion of a 5000-token handover doc:
  raw content (5000 tokens)
       │
       ▼
  Ollama generates 2–3 sentence summary (~50 tokens)
       │
       ├── summary  → stored + returned by search_memory() by default
       └── content  → stored in SQLite, returned only by get_memory(id)
```

| Layer              | Mechanism |
|---|---|
| Session startup    | Injects project index only (~150 tokens) — never full memories |
| Search results     | Returns summaries (~50 tokens each) — not full content |
| Full content       | Only fetched when Claude explicitly calls `get_memory(id)` |
| Ingestion          | Long docs summarised before storage |
| Deduplication      | External sources check last-modified before re-ingesting |
| Scheduling         | External pulls run on interval — never per-query |

---

## 5. MCP Tools

Six tools exposed to Claude via MCP SSE. Claude decides when to use them — they are not
auto-triggered on every message.

| Tool | Parameters | Returns |
|---|---|---|
| `search_memory` | `query`, `limit?`, `project?`, `type?`, `days?` | Summaries + metadata, ranked by relevance |
| `get_memory` | `id` | Full stored content for a specific entry |
| `add_memory` | `content`, `type`, `project`, `tags?` | Confirmation + assigned ID |
| `get_recent_context` | `project?`, `days?` | Last N entries chronologically |
| `list_projects` | — | Project index + active plugin status |
| `get_startup_summary` | — | Same compact summary as session-start injection |

### Hybrid search (search_memory internals)

```
query
  ├── FTS5 keyword search  → top 20 (BM25 scored)
  └── Ollama embed → ChromaDB cosine search → top 20
           │
           ▼
     Reciprocal Rank Fusion merges both lists
           │
           ▼
     top 10 results, summaries only, source + timestamp + id
```

Filters (`project`, `type`, `days`) applied before merging — narrows scope, reduces noise.

---

## 6. Dynamic Plugin System

Every external ingestion source is a **plugin**. The brain discovers which plugins are
usable at startup by running a health check. Only active plugins get scheduled.
No configuration or toggling required — if credentials are absent, the plugin is silently skipped.

### Discovery flow

```
Brain starts
    │
    ├── Confluence:   CONFLUENCE_URL + token set? endpoint reachable? → ✅ schedule
    ├── PagerDuty:    PD_TOKEN set? API reachable?                    → ✅ schedule
    ├── ClickHouse:   CH_URL set? test query succeeds?                → ❌ skip
    └── Jira:         JIRA_URL set?                                   → ❌ skip
```

Session hook ingestion and manual notes always work — zero external dependencies.

### Plugin contract

Each plugin module in `brain/app/ingestion/plugins/` implements three things:

```python
REQUIRED_ENV = ["PLUGIN_URL", "PLUGIN_TOKEN"]   # env vars needed
SCHEDULE     = "every 6h"                        # how often to pull
MEMORY_TYPE  = "confluence"                      # type tag on stored entries

async def health_check() -> bool: ...            # verify credentials + reachability
async def ingest(since: datetime) -> list[MemoryEntry]: ...  # pull + return entries
```

The plugin loader discovers all modules in the `plugins/` directory automatically.
Adding a new source = drop in a new file. No other changes required.

### `.env` — fully optional per plugin

```bash
# CORE — required
BRAIN_PORT=7741
OLLAMA_URL=http://ollama:11434

# PLUGIN: Confluence (optional)
CONFLUENCE_URL=https://confluence.derivco.co.za/
CONFLUENCE_TOKEN=

# PLUGIN: PagerDuty (optional)
PAGERDUTY_TOKEN=

# PLUGIN: ClickHouse (optional)
CLICKHOUSE_IOM_URL=
CLICKHOUSE_TOKEN=

# PLUGIN: Jira (optional)
JIRA_URL=
JIRA_TOKEN=
```

### Auto-detect from existing MCP config

```bash
brain setup --auto-detect
# Reads ~/.claude.json, detects mcp-atlassian → writes CONFLUENCE_URL + CONFLUENCE_TOKEN
# Detects clickhouse-iom → writes CLICKHOUSE_IOM_URL + CLICKHOUSE_TOKEN
# Outputs which plugins were auto-configured and which need manual setup
```

### Plugin status visible to Claude

`list_projects()` includes a plugin status preamble:

```
Active plugins:   confluence ✅  pagerduty ✅
Inactive plugins: clickhouse ❌ (no token)  jira ❌ (not configured)
```

Claude knows what sources are available on this machine.

---

## 7. Ingestion Pipelines

### Always-active (no plugin required)

| Source | Trigger | How |
|---|---|---|
| Session handover | Pre-compact hook | `POST /ingest/session` with handover markdown |
| Manual notes | CLI: `brain add "..."` | `POST /ingest/note` |
| File import | CLI: `brain import path` | `POST /ingest/file` — for seeding existing docs |

### Plugin-based (scheduled, only if active)

| Plugin | Schedule | What it pulls |
|---|---|---|
| Confluence | Every 6h | Pages created/modified by you in last 7 days |
| PagerDuty | Every 2h | Incidents assigned to you/your teams, resolved in last 48h |
| ClickHouse | Every 12h | (future) useful query results flagged manually |
| Jira | Every 6h | (stub) tickets assigned to you, recently updated |

All scheduled pulls deduplicate by `source` URL + `last_modified` — no re-ingestion of unchanged content.

---

## 8. Directory Structure

```
~/memorybrain/
├── docker-compose.yml
├── .env                              # machine-local, not committed
├── .env.example                      # committed, documents all options
├── README.md
│
├── brain/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py                   # FastAPI app entry point
│       ├── storage.py                # unified SQLite + ChromaDB abstraction
│       ├── search.py                 # hybrid RRF search
│       ├── summarise.py              # Ollama summarisation on ingestion
│       ├── ingestion/
│       │   ├── plugins/
│       │   │   ├── __init__.py       # plugin loader — auto-discovers all modules
│       │   │   ├── confluence.py
│       │   │   ├── pagerduty.py
│       │   │   ├── clickhouse.py
│       │   │   └── jira.py           # stub, ready when needed
│       │   ├── session.py            # /ingest/session endpoint (always active)
│       │   ├── manual.py             # /ingest/note + /ingest/file (always active)
│       │   └── scheduler.py          # registers only active plugin jobs
│       └── mcp/
│           └── tools.py              # MCP tool definitions + SSE handler
│
├── data/                             # Docker volume — machine-local, not committed
│   ├── brain.db                      # SQLite FTS5
│   └── chroma/                       # ChromaDB vectors
│
├── cli/
│   └── brain                         # bash CLI: brain add / brain import / brain setup
│
└── hooks/
    ├── session-ingest.sh             # replaces session-start-memory.sh
    └── pre-compact-ingest.py         # replaces pre-compact-auto-handover.py
```

---

## 9. MCP Configuration (Claude Code)

Add to `~/.claude.json` `mcpServers` section:

```json
"memorybrain": {
  "type": "sse",
  "url": "http://localhost:7741/sse",
  "headers": {}
}
```

No auth needed — service is local-only, bound to localhost.

---

## 10. Out of Scope (deliberate)

- **Cross-machine data sync** — each machine maintains its own independent brain. No sync, no conflicts.
- **Web UI** — Claude is the interface. No need for a browser dashboard.
- **Multi-user** — personal tool, single user.
- **Cloud hosting** — local Docker only. No internet exposure.
- **Real-time ClickHouse streaming** — ClickHouse plugin is manual-flag or scheduled, not streaming.

---

## 11. Resolved Decisions

1. **Summarisation model** → **Ollama `llama3.2:3b`** — added as second model in the ollama container alongside `nomic-embed-text`. Fully offline, no API cost. Expected RAM: ~2GB total for both models combined.

2. **Importance scoring** → **Auto-score via LLM at ingestion** — brain assigns 1–5 importance on every ingest (~1-2s latency per item). Score is stored and used to rank startup summary + search results. Overridable manually via `brain tag <id> importance=5`.

3. **Session project detection** → **Hybrid** — heuristic first (last meaningful path segment from CWD, e.g. `/mnt/c/git/_git/Monitoring` → `monitoring`), falls back to `unknown` if ambiguous (e.g. root or git dir). Repos can override by placing a `.brainproject` file in the root containing just the project slug. Hook checks for `.brainproject` before falling back to heuristic.
