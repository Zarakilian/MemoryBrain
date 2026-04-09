# MemoryBrain — How It Works

> **Status:** v0.4.0 ✅ — passive store, tool-agnostic.
> **Keep this file up to date** as features are added.

---

## Philosophy

MemoryBrain is a **passive, tool-agnostic memory store**. It does not pull from external systems. Claude retrieves data using its MCP tools (Confluence, ClickHouse, PagerDuty, etc.) and saves what it finds useful via `add_memory`. On a new machine with different MCP tools, MemoryBrain works identically — the memories reflect actual usage.

At session start, the session hook reads `~/.claude.json` directly on the host and injects a list of your registered MCP servers into the session. This provides context about what tools Claude has available — but MemoryBrain itself only stores what Claude explicitly saves.

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
│    embeddinggemma    — embeddings (~621MB, #1 MTEB sub-500M) │
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
2. **Container health check** — if brain is not running, prints a clear message with the exact `docker compose up -d` command to start it, then falls back to legacy `MEMORY.md` (no crash)
3. **Version check** — if `MEMORYBRAIN_DIR` is set (written by `brain setup`), compares `$MEMORYBRAIN_DIR/VERSION` against the running container's reported version. If they differ (e.g. after `git pull` without rebuilding), prints an update message with the exact rebuild command
4. **Subsystem readiness check** — calls `GET /readiness`, which checks all four subsystems: SQLite, ChromaDB, Ollama, and both required models (`embeddinggemma`, `llama3.2:3b`). If anything is degraded, prints a `## MemoryBrain — PARTIAL SERVICE` block listing exactly what failed, what still works, and the exact commands to fix it. On a healthy system, this step is completely silent
5. **Stamps the project MEMORY.md** — writes a `**MemoryBrain Last Active:** <ISO timestamp>` line to `~/.claude/projects/<hash>/memory/MEMORY.md`. This is the authoritative signal Claude reads at the start of every session to confirm MemoryBrain is active. The hash is derived from the CWD path (all non-alphanumeric characters replaced with `-`). If the MEMORY.md file doesn't exist for this project, the step is silently skipped.
6. Calls `GET /startup-summary` — brain returns a compact project index (~150 tokens): last activity per project
7. Hook injects it into your session — Claude is immediately oriented

**Degraded service modes** (reported at startup when detected):

| Condition | Available | Unavailable |
|---|---|---|
| Ollama down or models missing | Read + keyword search | `add_memory`, semantic search |
| ChromaDB down | Read + keyword search + `add_memory` | Semantic search |
| SQLite down | Nothing | Everything |

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

### Claude behavior rules (MANDATORY)

The session-start hook injects MemoryBrain context automatically, but Claude must also actively
call the MCP tools — not default to reading project files like `MEMORY.md` or `PROGRESS_LOG.md`.

**Required sequence at every session start:**

**Step 0 — Check the auto-loaded MEMORY.md (already in context):**
Look for `**MemoryBrain Last Active:**` at the top.
- Timestamp **< 7 days old** → MemoryBrain is active, proceed to Step 1
- Timestamp **missing or > 7 days old** → MemoryBrain likely offline, fall back to file-based memory

**Step 1 — Call MemoryBrain MCP tools:**
1. `mcp__memorybrain__get_startup_summary` — always first
2. `mcp__memorybrain__get_recent_context` (days=14) — for detailed recent activity

**After Step 1: STOP.** Do NOT read `MEMORY.md`, `PROGRESS_LOG.md`, or any other project files.
Only read a specific file if the user explicitly asks for it.

**Why the timestamp:** `MEMORY.md` and `PROGRESS_LOG.md` are fallbacks for when MemoryBrain is not
running. The `**MemoryBrain Last Active:**` timestamp is written to `MEMORY.md` by the session-start
hook every time MemoryBrain is confirmed healthy — giving Claude an explicit, file-based signal that
MemoryBrain is the authoritative source and no additional project files should be loaded.

**How this is enforced (three layers):**
- `~/.claude/CLAUDE.md` contains an explicit "Session Start Protocol (MANDATORY)" section with the timestamp-check decision tree
- The session-start hook prints a `## MANDATORY: MemoryBrain-first protocol` block as its last output, so the instruction is the most recent thing in Claude's injected context
- The `**MemoryBrain Last Active:**` timestamp in the auto-loaded `MEMORY.md` provides a file-based confirmation signal that Claude can check before making any decisions

### Session ends (pre-compact)
1. `pre-compact-auto-handover.py` hook fires
2. Reads handover content from stdin or the most recent `HANDOVER-*.md` file
3. POSTs it to `POST /ingest/session`
4. Brain: summarises → scores importance (1–5) → embeds → stores in SQLite + ChromaDB
5. **Stamps the project MEMORY.md** — same timestamp update as session start, confirming MemoryBrain was active during this session
6. The session is now permanently searchable

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

## MCP Tool Awareness

At session start, the `session-start-memory.sh` hook reads `~/.claude.json` **directly on the host** and injects a list of your registered MCP servers into the session context. Example:

```
## Available MCP Tools
- clickhouse-iom
- confluence-mcp
- memorybrain
- pagerduty

MemoryBrain will store what you retrieve with these tools.
```

This happens on the host — not inside Docker. `~/.claude.json` is never mounted into the container (it contains credentials). If `~/.claude.json` is missing or has no `mcpServers`, the block is silently skipped.

The `brain/app/mcp_discovery.py` module still exists and is unit-tested — it can be used by the CLI or future tooling. It is intentionally not exposed as an HTTP endpoint because the Docker container cannot access `~/.claude.json` (the `brain` user's home is `/app`, not the host home). Host-side execution is both simpler and more secure.

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

> You can clone to any path — the CLI and hooks resolve paths dynamically from `__file__` / CWD. `~/memorybrain` is conventional but not required.

---

---

### Option A — One-command setup (recommended)

After cloning, run:

```bash
cp .env.example .env
python3 cli/brain.py setup --auto-detect
```

This handles Steps 3–8 automatically: starts Docker, pulls models, registers the MCP server, installs hooks + skills, adds the `brain` shell alias. Skip to Step 8 (tag your projects) when done.

---

### Option B — Manual setup (Steps 2–7)

### Step 2 — Configure environment

```bash
cp .env.example .env
```

Open `.env` if you need to change the port or add API key authentication. All fields are optional:

```bash
# Required
BRAIN_PORT=7741          # change if 7741 is in use
OLLAMA_URL=http://ollama:11434   # leave as-is for Docker Compose setup

# Authentication (optional but recommended)
# Set to any random string — enables X-Brain-Key header check on all endpoints
# Leave blank to run open (fine for single-user localhost-only use)
BRAIN_API_KEY=
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

### Step 4 — Pull Ollama models (first time only, ~2.6GB)

```bash
docker compose exec ollama ollama pull embeddinggemma
docker compose exec ollama ollama pull llama3.2:3b
```

This is a one-time download. Models are stored in a Docker volume (`ollama_data`) and persist across container restarts.

Verify:
```bash
docker compose exec ollama ollama list
# Should show: embeddinggemma, llama3.2:3b
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

### Step 6 — Install hooks and skills

The `brain setup --auto-detect` command (Option A above) handles this automatically. To install manually:

```bash
# Hooks — replace existing flat-file hooks
cp hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
chmod +x ~/.claude/hooks/session-start-memory.sh
chmod +x ~/.claude/hooks/pre-compact-auto-handover.py

# Skills — copy to ~/.claude/skills/
mkdir -p ~/.claude/skills/log-everything
cp skills/log-everything/SKILL.md ~/.claude/skills/log-everything/SKILL.md
```

Skills included:
| Skill | Trigger | What it does |
|---|---|---|
| `log-everything` | `/log-everything` | Generates session summary → saves via `add_memory` → prompts for next-session notes |

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

## The `brain` CLI ✅

After running `brain setup`, a `brain` alias is available in your terminal:

```bash
brain add "just discovered X about Y"       # store a note from anywhere
brain import ~/Downloads/some-doc.md        # import a file
brain seed                                  # bulk import MEMORY.md + HANDOVER files from CWD
brain status                                # check brain health + service version
brain setup --auto-detect                   # re-run setup (safe on any machine)
```

On a fresh machine, the full setup is one command:
```bash
python3 ~/path/to/MemoryBrain/cli/brain.py setup --auto-detect
```

This single command: starts Docker containers, pulls Ollama models, registers the MCP server with Claude Code, installs session hooks, installs skills, and adds the `brain` shell alias.

### Embedding model

MemoryBrain uses **EmbeddingGemma** (`embeddinggemma` via Ollama) for semantic search — the #1 ranked sub-500M embedding model on MTEB benchmarks, at ~621MB.

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
docker compose up -d --build
```

Both data (`memorybrain_brain_data`) and model (`memorybrain_ollama_data`) volumes are preserved — no re-download needed. Open a new Claude session after the upgrade.

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
| SQLite database | Docker named volume `memorybrain_brain_data` |
| ChromaDB vectors | Docker named volume `memorybrain_brain_data` (subdirectory) |
| Ollama models | Docker named volume `memorybrain_ollama_data` |
| Config | `.env` in repo root (machine-local, never committed) |

Data lives in Docker named volumes — not in the repo directory. This ensures data survives container recreation.

**To back up your memories:**
```bash
docker run --rm -v memorybrain_brain_data:/data -v $(pwd):/backup alpine \
  tar czf /backup/brain-backup-$(date +%Y%m%d).tar.gz -C /data .
```

**To restore:**
```bash
docker run --rm -v memorybrain_brain_data:/data -v $(pwd):/backup alpine \
  tar xzf /backup/brain-backup-YYYYMMDD.tar.gz -C /data
```

**Data is machine-local by design.** Work PC and home PC maintain independent memory databases. The code (this repo) is shared; the memories are not.

---

## Restart behavior

| Command | What happens | Data safe? | MCP session? |
|---|---|---|---|
| `docker compose restart brain` | Stops + restarts container process | ✅ Yes | ⚠️ Breaks — open new Claude session |
| `docker compose up -d` | No change if config unchanged; recreates if changed | ✅ Yes (named volume) | ⚠️ Breaks if recreated |
| `docker compose up -d --force-recreate` | Always recreates container | ✅ Yes (named volume) | ⚠️ Breaks — open new Claude session |
| `docker compose down && up -d` | Stops + removes containers, restarts | ✅ Yes (named volume) | ⚠️ Breaks — open new Claude session |

**MCP session note:** When the brain container restarts mid-session, the SSE connection drops. The MCP client reconnects but the server-side session state is reset — tool calls will fail with "initialization not complete". Open a new Claude Code session to restore full MCP functionality.
