# MemoryBrain

Persistent searchable memory service for AI assistants (Claude Code, Gemini/Antigravity) via MCP.

Replaces flat MEMORY.md files with a FastAPI + SQLite FTS5 + ChromaDB + Ollama service
that gives your AI assistant automatic context on every new session, with on-demand semantic search.

MemoryBrain natively supports **SSE transport** for Claude Code and **stdio transport** (via a Docker wrapper) for Gemini.

MemoryBrain is a **passive store** — it stores what your assistant saves via `add_memory`. No polling,
no plugin credentials. Works identically on any machine with any MCP tools registered.

## Quick Start

```bash
git clone https://github.com/Zarakilian/MemoryBrain ~/memorybrain
cd ~/memorybrain
cp .env.example .env
# One command does everything (Docker, models, MCP, hooks, skills):
python3 cli/brain.py setup --auto-detect
```

Or manually, step by step — see [HOW_IT_WORKS.md](HOW_IT_WORKS.md) for the full setup guide.

## Project detection

Every memory stored in MemoryBrain belongs to a **project**. The project slug is a short label
(e.g. `api-service`, `mobile-app`) that scopes memories so they don't bleed across unrelated work.

When Claude calls `add_memory`, it tags the memory with the current project slug. When it calls
`get_recent_context` or `search_memory`, it can filter by that slug — so your API service memories
stay separate from your infrastructure memories, and each session starts with context that is
actually relevant to what you are working on right now.

**Two ways the slug is determined, in priority order:**

1. **`.brainproject` file** — create a file in your project root containing just the slug name.
   This is explicit and reliable. MemoryBrain reads it every session.

   ```bash
   echo "my-project-name" > /path/to/project/.brainproject
   ```

2. **Automatic fallback** — if no `.brainproject` file is present, MemoryBrain uses the last
   meaningful segment of your current working directory path as the slug. For example, if you are
   working in `/home/user/projects/api-service`, the slug becomes `api-service`.

The `.brainproject` file approach is recommended for any project you work in regularly, because
it gives you a stable, intentional slug that won't change if you rename or move the directory.

## Skills included

| Skill | Trigger | What it does |
|---|---|---|
| `log-everything` | `/log-everything` | Generates session summary → saves via `add_memory` → prompts for next-session notes |
| `handover` | `/handover` | Creates comprehensive session handover document → saves to MemoryBrain or file |
| `map-project-files` | `/map-project-files` | Discovers high-priority `.md` files for the project → saves a file map as a reference memory so future sessions know exactly where to look |

## MCP Tools available in Claude & Gemini

| Tool | Description |
|---|---|
| `search_memory` | Hybrid keyword+semantic search — returns summaries |
| `get_memory` | Full content fetch by ID |
| `add_memory` | Store a new note/fact |
| `get_recent_context` | Recent entries by project |
| `list_projects` | All projects + last activity |
| `get_startup_summary` | Compact session-start injection |

> **Session start rule:** At session start, Claude checks the auto-loaded `MEMORY.md` for a
> `**MemoryBrain Last Active:**` timestamp. If fresh (< 7 days), it calls `get_startup_summary`
> then `get_recent_context` — and then **stops**. No project files are read. The timestamp is
> written by the session-start hook every time MemoryBrain is confirmed healthy, giving Claude an
> explicit signal to trust MemoryBrain over stale file-based memory. See [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

## What's New in v0.5.0

- **Semantic supersession** — Auto-archives stale memories using type-aware similarity thresholds (e.g., facts need higher confidence than sessions). Audit trail preserved; nothing is deleted.
- **Recency decay** — Recent memories rank higher in search results. Configurable via `RECENCY_DECAY_RATE` env var.
- **Tag + type filters** — `search_memory` gains `tags` and `type_filter` parameters for faster, more precise lookups.
- **Multi-AI provider support** — Use Ollama (local, default), Gemini, or OpenAI-compatible endpoints for summarization and embeddings. Provider auto-selected from env vars.
- **Versioned migrations** — Schema changes apply automatically at container startup. No manual SQL needed.
- **`brain update` command** — One-step upgrade: pulls latest, rebuilds Docker, reinstalls hooks and skills.
- **`delete_memory` MCP tool** — Hard delete for wrong entries (vs. supersession for stale ones).
- **Per-project session context** — `get_startup_summary` now shows the most recent memory for each project.

## Architecture

- **FastAPI** on port 7741
- **SQLite FTS5** for keyword search + storage
- **ChromaDB** for semantic vector search
- **Provider-based AI backend:** Ollama (default, local), Gemini, or OpenAI-compatible for embeddings + summarisation
- **Hybrid search**: FTS5 keywords + ChromaDB cosine → Reciprocal Rank Fusion, with recency decay
- **Semantic supersession engine**: Type-aware thresholds, audit trail, automatic archive on ingest
- **Migration system**: Versioned SQL migrations in `brain/migrations/` — applied at startup, idempotent
- **MCP SSE** at `http://localhost:7741/sse`
- **Data**: machine-local only — not synced across machines
- **`GET /mcp-tools`**: Reports registered MCP servers from `~/.claude.json`; called by the session-start hook
