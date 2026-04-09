# MemoryBrain

Persistent searchable memory service for Claude Code via MCP SSE.

Replaces the flat MEMORY.md system with a FastAPI + SQLite FTS5 + ChromaDB + Ollama service
that gives Claude automatic context on every new session, with on-demand semantic search.

MemoryBrain is a **passive store** — it stores what Claude saves via `add_memory`. No polling,
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

Place a `.brainproject` file in any project root containing just the project slug:
```
monitoring
```
If absent, the last path segment of CWD is used as the project slug.

## MCP Tools available in Claude

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

## Architecture

- **FastAPI** on port 7741
- **SQLite FTS5** for keyword search + storage
- **ChromaDB** for semantic vector search
- **Ollama** (`embeddinggemma` + `llama3.2:3b`) for embeddings + summarisation
- **Hybrid search**: FTS5 keywords + ChromaDB cosine → Reciprocal Rank Fusion
- **MCP SSE** at `http://localhost:7741/sse`
- **Data**: machine-local only — not synced across machines
- **`GET /mcp-tools`**: Reports registered MCP servers from `~/.claude.json`; called by the session-start hook
