# MemoryBrain — How It Works

> **Status:** Part 1 (Core) ✅ + Part 2 (Plugins + CLI) ✅ complete.
> **Keep this file up to date** as features are added.

---

## What problem does this solve?

Every Claude Code session starts completely blank. You manually re-explain context via MEMORY.md files — which have a hard 200-line truncation limit, no search, and no connection to external tools like Confluence or PagerDuty.

MemoryBrain replaces that with a persistent, searchable memory service that:
- **Automatically orients Claude** at the start of every session (~150 tokens, not 200 lines)
- **Lets Claude search** across all your past sessions, notes, and external sources
- **Summarises everything on the way in** — long handovers become short, searchable entries
- **Runs entirely locally** — your data never leaves your machine
- **Is portable** — clone this repo on any machine, run one command, and it works

---

## Architecture overview

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Code (any machine)                                   │
│                                                              │
│  session-start hook ──────────► GET /startup-summary        │
│  pre-compact hook  ──────────► POST /ingest/session         │
│  MCP tools (during work) ────► /sse  (6 tools)              │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP localhost:7741
┌─────────────────────────▼───────────────────────────────────┐
│  Brain container  (FastAPI)                                  │
│                                                              │
│  Ingest pipeline:                                            │
│    content ──► Ollama summarise ──► Ollama embed             │
│             ──► SQLite FTS5 (keyword)                        │
│             ──► ChromaDB (semantic vectors)                  │
│                                                              │
│  Search pipeline:                                            │
│    query ──► FTS5 keyword search (top 20)                   │
│          ──► Ollama embed ──► ChromaDB cosine (top 20)       │
│          ──► Reciprocal Rank Fusion ──► top 10 summaries     │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  Ollama container                                            │
│    nomic-embed-text  — 768-dim embeddings (~274MB)           │
│    llama3.2:3b       — summarisation + importance (~2GB)     │
└─────────────────────────────────────────────────────────────┘
```

### Storage split

| Store | What goes in | Used for |
|---|---|---|
| SQLite FTS5 (`data/brain.db`) | All content, summaries, metadata | Keyword search, CRUD, full content fetch |
| ChromaDB (`data/chroma/`) | Embeddings + memory ID reference | Semantic/similarity search |

Both stores are in the `data/` directory — a Docker volume on your machine. **Data is machine-local and never synced.** The code is portable; your memories stay on each machine.

---

## Data flow — what happens when you work

### Session starts
1. `session-start-memory.sh` hook fires
2. Calls `GET /startup-summary` on the brain
3. Brain returns a compact project index (~150 tokens): last activity per project
4. Hook injects it into your session — Claude is immediately oriented
5. If brain is not running, falls back to legacy `MEMORY.md` (no crash)

### During a session
Claude calls MCP tools on demand:

| Tool | What it does |
|---|---|
| `search_memory(query)` | Hybrid keyword+semantic search → returns **summaries** (not full content) |
| `get_memory(id)` | Fetch **full content** of one specific entry |
| `add_memory(content, type, project)` | Store a new note or fact right now |
| `get_recent_context(project, days)` | Chronological recent entries for a project |
| `list_projects()` | All known projects + last activity |
| `get_startup_summary()` | Same compact index as session start |

### Session ends (pre-compact)
1. `pre-compact-auto-handover.py` hook fires
2. Reads handover content from stdin or the most recent `HANDOVER-*.md` file
3. POSTs it to `POST /ingest/session`
4. Brain: summarises → scores importance (1–5) → embeds → stores in SQLite + ChromaDB
5. The session is now permanently searchable

### Why summaries, not full content in search results
A 5000-token handover becomes a ~50-token summary on ingest. Search returns summaries only. Claude calls `get_memory(id)` only for entries it actually needs in full. This keeps the context window lean.

---

## Hybrid search — how it works

```
query: "grafana clickhouse dashboard"
    │
    ├── FTS5 keyword search → BM25 ranked → top 20
    │
    └── embed(query) → ChromaDB cosine → top 20
                │
                ▼
        Reciprocal Rank Fusion
        score = Σ  1 / (60 + rank + 1)  per list
                │
                ▼
        top 10 results (entries in BOTH lists rank highest)
```

Entries that match both keyword and semantic search rank highest. Keyword-only and semantic-only hits are included but ranked lower.

---

## Project detection

The brain needs to know which project a session belongs to. It uses this order:

1. **`.brainproject` file** in the CWD — contains just the project slug (e.g. `monitoring`)
2. **Heuristic** — last meaningful path segment of CWD (e.g. `/mnt/c/git/_git/Monitoring` → `monitoring`)

To explicitly tag a repo, create a `.brainproject` file:
```bash
echo "my-project-name" > .brainproject
```

---

## Part 2 — Plugins (coming next)

> Status: design complete, not yet implemented.

Plugins automatically pull from external sources and store them as memories — no manual steps required.

| Plugin | Schedule | What it pulls |
|---|---|---|
| Confluence | Every 6h | Pages you created/modified in the last 7 days |
| PagerDuty | Every 2h | Incidents assigned to you/your teams, resolved in last 48h |
| ClickHouse | Every 12h | (future) query results you flag manually |
| Jira | Every 6h | (stub) tickets assigned to you, recently updated |

**Plugins auto-detect from `.env`** — if a plugin's credentials are absent, it's silently skipped. No toggling required. On a work machine with Confluence access, the plugin runs. On a personal machine without it, it doesn't.

Also in Part 2: the `brain` CLI (`brain add "..."`, `brain import file.md`, `brain setup --auto-detect`).

---

## Setup — any machine

> This section is written so Claude can follow it autonomously.
> If you're asking Claude to set this up, point it here.

### Prerequisites

| Requirement | Check command | Install if missing |
|---|---|---|
| Docker | `docker --version` | Install Docker Desktop (Mac/Windows) or Docker Engine (Linux) |
| Docker running | `docker ps` | Start Docker Desktop / Rancher Desktop |
| Git | `git --version` | Usually pre-installed |
| curl | `curl --version` | Usually pre-installed |
| ~3GB disk space | `df -h ~` | For Ollama models |

**WSL users (Windows):** Docker must be accessible from WSL. If `docker: command not found` in WSL, enable WSL integration in Docker Desktop or Rancher Desktop → Preferences → WSL Integrations.

---

### Step 1 — Clone the repo

```bash
git clone https://github.com/Zarakilian/MemoryBrain ~/memorybrain
cd ~/memorybrain
```

> Clone to `~/memorybrain` so the hooks and scripts know where to find it. You can use a different path but must update `MEMORYBRAIN_PATH` in the hooks.

---

### Step 2 — Configure environment

```bash
cp .env.example .env
```

Open `.env` and fill in credentials for any external services you want to use. Everything is optional except the core variables:

```bash
# Required
BRAIN_PORT=7741          # change if 7741 is in use
OLLAMA_URL=http://ollama:11434   # leave as-is for Docker Compose setup

# Authentication (optional but recommended)
# Set to any random string — enables X-Brain-Key header check on all endpoints
# Leave blank to run open (fine for single-user localhost-only use)
BRAIN_API_KEY=

# Optional — fill in to enable plugins
CONFLUENCE_URL=https://your-confluence.example.com/
CONFLUENCE_TOKEN=your-personal-access-token

PAGERDUTY_TOKEN=your-pd-api-token
```

---

### Step 3 — Start the service

```bash
docker compose up -d
```

This starts:
- `brain` container (FastAPI on port 7741)
- `ollama` container (Ollama model server)

Verify both are running:
```bash
docker compose ps
curl http://localhost:7741/health
# Expected: {"status":"ok"}
```

---

### Step 4 — Pull Ollama models (first time only, ~2.3GB)

```bash
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
```

This is a one-time download. Models are stored in a Docker volume (`ollama_data`) and persist across container restarts.

Verify:
```bash
docker compose exec ollama ollama list
# Should show: nomic-embed-text, llama3.2:3b
```

---

### Step 5 — Add MemoryBrain as an MCP server in Claude Code

```bash
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
```

Verify it's registered:
```bash
claude mcp list
# Should show: memorybrain   http://localhost:7741/sse
```

---

### Step 6 — Install the session hooks

These replace the existing flat-file memory hooks. **Back up your existing hooks first if you have custom ones.**

```bash
# Back up existing hooks (if any)
cp ~/.claude/hooks/session-start-memory.sh ~/.claude/hooks/session-start-memory.sh.backup 2>/dev/null || true
cp ~/.claude/hooks/pre-compact-auto-handover.py ~/.claude/hooks/pre-compact-auto-handover.py.backup 2>/dev/null || true

# Install MemoryBrain hooks
cp ~/memorybrain/hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp ~/memorybrain/hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py

# Make executable
chmod +x ~/.claude/hooks/session-start-memory.sh
chmod +x ~/.claude/hooks/pre-compact-auto-handover.py
```

---

### Step 7 — Verify end-to-end

```bash
# Store a test note
curl -s -X POST http://localhost:7741/ingest/note \
  -H "Content-Type: application/json" \
  -d '{"content":"MemoryBrain is installed and working on this machine","project":"personal","tags":["setup"]}'
# Expected: {"id":"...","summary":"...","importance":3}

# Check startup summary
curl -s http://localhost:7741/startup-summary
# Expected: {"summary":"# MemoryBrain — Session Context\n- personal: ..."}
```

Open a **new Claude Code session**. You should see the session context injected automatically at the top.

---

### Step 8 — (Optional) Tag projects

For any repo you work in, create a `.brainproject` file so sessions are correctly attributed:

```bash
echo "monitoring" > /path/to/your/project/.brainproject
echo "memorybrain" > ~/memorybrain/.brainproject
```

---

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
python3 ~/path/to/MemoryBrain/cli/brain.py setup --auto-detect
```

### Plugins (Confluence + PagerDuty)

Plugins run on a schedule inside the brain container. They auto-activate if credentials
are present in `.env`, and are silently skipped if not.

| Plugin | Schedule | What it pulls |
|---|---|---|
| Confluence | Every 6h | Pages you authored or last modified |
| PagerDuty | Every 2h | Incidents assigned to you, resolved |

**Credentials in `.env`:**
```bash
CONFLUENCE_URL=https://your-confluence.example.com/
CONFLUENCE_TOKEN=your-personal-access-token

PAGERDUTY_TOKEN=your-pd-api-token
```

Run `brain setup --auto-detect` to extract these automatically from `~/.claude.json`.

### Embedding model

MemoryBrain uses **EmbeddingGemma** (`embeddinggemma` via Ollama) for semantic search — the #1 ranked sub-500M embedding model on MTEB benchmarks, at ~200MB (smaller than the previous `nomic-embed-text`).

---

## Security

MemoryBrain runs on loopback (`127.0.0.1`) and is designed for single-user local use. Several protections are in place as of v0.3.0:

| Protection | Details |
|---|---|
| **Loopback binding** | Port bound to 127.0.0.1 — not reachable from other machines on your network |
| **Optional API key** | Set `BRAIN_API_KEY` in `.env` to require `X-Brain-Key` header on all requests |
| **OLLAMA_URL validation** | Must be `http://` or `https://` — rejects `file://`, `ftp://`, etc. at startup |
| **Hook URL validation** | Both Claude Code hooks refuse to connect to non-localhost `MEMORYBRAIN_URL` |
| **Input validation** | Memory type must be in allowed enum; project slug regex-validated; content capped at 100K chars; tags bounded at 20 items |
| **Content deduplication** | Same content+project hash won't be ingested twice — hooks are idempotent |
| **Concurrency limit** | Max 3 concurrent ingest pipeline runs (asyncio semaphore) |
| **Non-root container** | Brain container runs as dedicated `brain` user, not root |
| **Cross-store rollback** | If ChromaDB write fails after SQLite insert, the SQLite entry is deleted |

If you use `BRAIN_API_KEY`, add the header to any direct API calls:
```bash
curl -H "X-Brain-Key: your-key" http://localhost:7741/status
```

The Claude Code hooks read `BRAIN_API_KEY` from env automatically and pass it as a header — no extra config needed.

---

## Upgrading

```bash
cd ~/memorybrain
git pull
docker compose down
docker compose up -d --build
```

Models are preserved in the `ollama_data` volume — no re-download needed.

---

## Troubleshooting

### Brain container won't start

```bash
docker compose logs brain
```

Common causes:
- Port 7741 already in use → change `BRAIN_PORT` in `.env`
- `.env` file missing → `cp .env.example .env`

### Ollama models not found (500 errors on ingest)

```bash
docker compose exec ollama ollama list
```

If models are missing, re-run Step 4.

### Hook not firing

```bash
ls -la ~/.claude/hooks/
```

Verify `session-start-memory.sh` and `pre-compact-auto-handover.py` exist and are executable (`-rwxr-xr-x`).

### MCP server not showing in Claude

```bash
claude mcp list
```

If missing, re-run Step 5. If present but not connecting, verify the brain is running: `curl http://localhost:7741/health`.

### WSL: `docker: command not found`

Enable WSL integration in Docker Desktop / Rancher Desktop:
- Docker Desktop → Settings → Resources → WSL Integration → enable your distro
- Rancher Desktop → Preferences → WSL → Integrations → enable your distro

Then restart WSL: `wsl --shutdown` from PowerShell, reopen terminal.

---

## Data location

| What | Where |
|---|---|
| SQLite database | `~/memorybrain/data/brain.db` (via Docker volume) |
| ChromaDB vectors | `~/memorybrain/data/chroma/` (via Docker volume) |
| Ollama models | Docker volume `ollama_data` |
| Config | `~/memorybrain/.env` (machine-local, never committed) |

**To back up your memories:** copy `~/memorybrain/data/` somewhere safe.

**Data is machine-local by design.** Work PC and home PC maintain independent memory databases. The code (this repo) is shared; the memories are not.
