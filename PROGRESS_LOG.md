# MemoryBrain — Progress Log

**READ THIS FIRST at the start of every session.**

---

## Status: Part 1 (Core) — COMPLETE ✅

**GitHub:** https://github.com/Zarakilian/MemoryBrain
**Latest tag:** `v0.1.0` (2026-03-27)
**Tests:** 38 passing
**Docker:** Builds and runs — health + startup-summary verified

---

## What was built (Part 1)

### The core brain service

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

### Known issues / debt (non-blocking)
- `datetime.utcnow()` deprecation warnings (Python 3.12+) — models.py, storage.py
- `score_importance` always overwrites caller-supplied importance (asymmetric with `summary` handling)
- No file size limit on `/ingest/file` endpoint
- Ollama unavailability returns 500 with no friendly message

---

## What's next (Part 2 — Plugins + CLI)

Plan to be written: `docs/superpowers/plans/2026-03-27-memorybrain-plugins.md`

| Track | Description | Status |
|---|---|---|
| Confluence plugin | Ingest pages modified in last 7 days, every 6h | ⬜ |
| PagerDuty plugin | Ingest incidents assigned to you/team, every 2h | ⬜ |
| APScheduler | Wire plugin scheduler into FastAPI lifespan | ⬜ |
| Plugin loader | Auto-detect from `.env`, health-check, skip if absent | ⬜ |
| `brain` CLI | `brain add`, `brain import`, `brain setup --auto-detect` | ⬜ |

---

## Session log

### 2026-03-27 — Part 1 built (subagent-driven, 10 tasks)

**What happened:**
- Design doc recovered from Claude file history (previous session had never saved it to disk)
- Design doc saved to `docs/design.md`
- Implementation plan written: `docs/superpowers/plans/2026-03-27-memorybrain-core.md`
- All 10 tasks implemented via subagent-driven development with spec + quality review per task
- One critical bug caught and fixed during execution: `async def` calling sync ollama client — switched to `ollama.AsyncClient` with proper `await`
- One production bug caught during smoke test: missing `init_db()` call at startup — fixed with FastAPI lifespan hook
- Pushed to GitHub as v0.1.0

**To install on this machine:**
```bash
cd /mnt/c/git/_git/MemoryBrain
cp .env.example .env
docker compose up -d
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
cp hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
```

---

## Key facts for future sessions

- Port: `7741` (configurable via `BRAIN_PORT` in `.env`)
- DB: `./data/brain.db` (SQLite, Docker volume)
- Vectors: `./data/chroma/` (Docker volume)
- Ollama models needed: `nomic-embed-text` (embeddings, ~274MB) + `llama3.2:3b` (summarisation, ~2GB)
- Project slug detection: `.brainproject` file in repo root (fallback: last CWD segment)
- This repo's slug: `memorybrain` (see `.brainproject`)
- MCP SSE URL: `http://localhost:7741/sse`
