# MemoryBrain

Persistent searchable memory service for Claude Code via MCP SSE.

Replaces the flat MEMORY.md system with a FastAPI + SQLite FTS5 + ChromaDB + Ollama service
that gives Claude automatic context on every new session, with on-demand semantic search.

## Quick Start

```bash
cd ~/memorybrain  # or wherever you cloned this
cp .env.example .env
docker compose up -d
# Pull Ollama models (first time only — ~2GB download):
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
# Add MCP server to Claude Code:
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
# Install hooks (replaces existing flat-file hooks):
cp hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
```

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

## Architecture

- **FastAPI** on port 7741
- **SQLite FTS5** for keyword search + storage
- **ChromaDB** for semantic vector search
- **Ollama** (`nomic-embed-text` + `llama3.2:3b`) for embeddings + summarisation
- **Hybrid search**: FTS5 keywords + ChromaDB cosine → Reciprocal Rank Fusion
- **MCP SSE** at `http://localhost:7741/sse`
- **Data**: machine-local only — not synced across machines
