# MemoryBrain — Semantic Supersession & Search Improvements Design

**Date:** 2026-04-21
**Branch:** `feature/semantic-supersession-improvements`
**Status:** Approved — ready for implementation planning
**Base:** `origin/master` (v0.4.1 — includes Gemini MCP stdio + OAuth fix)

> **Already shipped in v0.4.1 (not in scope here):**
> - MCP stdio transport (`brain/stdio_server.py`) — lets Gemini CLI connect to MemoryBrain
> - Gemini CLI auto-registration in `brain setup` (`~/.gemini/antigravity/mcp_config.json`)
> - Auth middleware: public paths (`/sse`, `/health`, `/readiness`) bypass API key check
>
> **This spec covers the next layer of improvements on top of v0.4.1.**

---

## Problem Statement

Three pain points identified from active usage across multiple projects:

1. **Search quality** — The llama3.2:3b summariser generates generic 3-sentence summaries that don't preserve domain-specific facts (table names, server names, error codes). Stale memories rank equally to recent ones. Tags and type are stored but not searchable.
2. **Memory lifecycle** — No way to update or supersede a stale memory. Corrections require saving a new memory alongside the old one. Over time this creates noise that misleads retrieval.
3. **Token efficiency** — `get_startup_summary` gives no per-project state hint. `get_recent_context` loads all entries blind with no pre-filtering by relevance.

---

## Design

### Section 1 — Data Model

Three new columns added to the `memories` table in SQLite:

| Column | Type | Default | Purpose |
|---|---|---|---|
| `status` | TEXT | `'active'` | `'active'` or `'archived'` |
| `superseded_by` | TEXT | NULL | ID of the memory that replaced this one |
| `supersedes` | TEXT | NULL | ID of the memory this one replaced |

ChromaDB gets a `status` metadata field on all new writes for filter support.

Migrations are managed via a versioned system — `brain/migrations/` folder with numbered SQL files (e.g. `001_add_status_supersession.sql`). A `migrations.py` runner executes unapplied migrations at FastAPI startup, idempotently. This means any `docker compose up -d --build` automatically applies new schema changes — no manual SQL required on any machine.

The `add_memory` response gains two new fields:

```json
{
  "id": "abc123",
  "summary": "...",
  "importance": 4,
  "superseded": ["old-id-1"],
  "potential_supersessions": [
    {"id": "old-id-2", "similarity": 0.82, "summary": "..."}
  ]
}
```

---

### Section 2 — Semantic Supersession Engine

On every ingest, **after** embedding but **before** writing to storage:

1. Query ChromaDB for the top 5 most similar active memories in the same project
2. For each result, apply type-aware threshold (see table below)
3. At **auto-archive threshold**: mark old memory `status='archived'`, set `superseded_by=new_id`, set `supersedes=old_id` on the new entry. Update both SQLite and ChromaDB metadata atomically (existing rollback pattern preserved).
4. At **warn-only threshold**: old memory untouched, result added to `potential_supersessions` in response.
5. Below warn threshold: treated as new, independent memory.

**Type-aware thresholds:**

| Memory type | Auto-archive above | Warn-only above | Notes |
|---|---|---|---|
| `session` | 0.80 | 0.70 | Sessions naturally replace prior sessions |
| `handover` | 0.80 | 0.70 | Same as session |
| `note` | 0.90 | 0.75 | Notes are often additive — higher bar |
| `fact` | 0.92 | 0.78 | Facts need highest confidence before archiving |
| `file` | 0.85 | 0.72 | File imports may update prior versions |
| `reference` | Never | 0.80 | References are always additive — never auto-archived |

**Multiple supersessions:** If a new memory exceeds threshold against several old ones (e.g. a comprehensive summary replacing several fragmented notes), all are archived. The `supersedes` field on the new memory stores the most-similar one; all superseded IDs are in the response.

**Audit trail:** Archived memories are **never deleted**. They remain in SQLite and ChromaDB, queryable with `include_history=True`. The full correction lineage is always recoverable.

---

### Section 3 — Search Improvements

**A — Exclude archived by default**
All FTS5 and ChromaDB queries add `WHERE status = 'active'`. New `include_history=False` parameter on `search_memory` — when `True`, removes the filter and includes archived results labelled `[archived]`.

**B — Recency decay on RRF scores**
After RRF fusion, each result score is multiplied by a decay factor:
```
recency_factor = 1 / (1 + days_since_created × RECENCY_DECAY_RATE)
```
Default `RECENCY_DECAY_RATE=0.02` (configurable in `.env`). Effect: 7-day-old memory scores ~88% of a same-day memory; 30-day-old scores ~60%. Decay is gentle — highly relevant old content still surfaces.

**C — Tag and type filters**
`search_memory` gains two optional parameters:
- `tags: list[str]` — OR logic, matches memories containing any of the provided tags
- `type_filter: str` — exact match on memory type

Both filter at the SQLite FTS5 layer before semantic search, reducing the candidate pool cheaply.

---

### Section 4 — MCP Tool Changes

**Updated: `search_memory`**
New parameters: `include_history: bool = False`, `tags: list[str] = None`, `type_filter: str = None`.

**Updated: `add_memory`**
- New optional `description: str` parameter. If provided, this string is used as the summary directly — the LLM summariser is bypassed for that field. Importance scoring still runs unless content is very short. This allows Claude (as the caller) to write a precise, domain-aware one-liner instead of relying on llama3.2:3b.
- Response now includes `superseded` and `potential_supersessions`.

**New: `delete_memory(id: str)`**
Hard delete — removes from SQLite and ChromaDB. For genuinely wrong entries, not just stale ones. Returns `{"deleted": true, "id": "..."}`.

**Updated: `get_startup_summary`**
Each project entry gains a `recent_state` field — the summary of the most recent active memory for that project. Adds ~10–15 tokens per project. Example output:
```
- support (last: 2026-04-21): Fix 02 revised to use EXCEPT approach matching IOMQF1 production SP
- _git (last: 2026-04-21): FeatureID28 deployed to Betway and Baytree
```

---

### Section 5 — Summarisation Quality (Gemini AI Provider Support)

> **Note:** This is distinct from the Gemini CLI MCP transport already shipped in v0.4.1.
> That feature lets Gemini CLI *use* MemoryBrain as a tool.
> This feature lets MemoryBrain *use* Gemini (or OpenAI) as its AI backend for summarisation and embeddings.

The current `summarise.py` is hard-coded to Ollama (`llama3.2:3b` for summarisation, `embeddinggemma` for embeddings). This section refactors it into a provider-abstraction layer supporting three backends:

| Provider | Env vars required | Used for |
|---|---|---|
| **Ollama** (default) | `OLLAMA_URL` | Both summarisation and embeddings — no API key, fully local |
| **Gemini** | `GOOGLE_API_KEY`, `GEMINI_EMBED_MODEL`, `GEMINI_SUMMARISE_MODEL` | Summarisation and/or embeddings via Google AI |
| **OpenAI-compatible** | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_EMBED_MODEL`, `OPENAI_SUMMARISE_MODEL` | Any OpenAI-compatible endpoint |

Provider selection is automatic: if `GOOGLE_API_KEY` is set, Gemini is used. If `OPENAI_API_KEY` is set, OpenAI-compatible is used. If neither is set, Ollama is used. Both summarisation and embeddings can use different providers if needed (controlled separately via env vars).

The abstraction lives in `brain/app/summarise.py` as a provider protocol — each backend implements `embed(text)` and `summarise(content, max_sentences)`. The ingest pipeline calls the protocol; no other files change.

`.env.example` gains the new optional variables:
```bash
# Gemini provider (optional — if set, used instead of Ollama)
GOOGLE_API_KEY=
GEMINI_EMBED_MODEL=models/text-embedding-004
GEMINI_SUMMARISE_MODEL=gemini-2.0-flash

# OpenAI-compatible provider (optional)
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_EMBED_MODEL=text-embedding-3-small
OPENAI_SUMMARISE_MODEL=gpt-4o-mini
```

`requirements.txt` gains optional dependencies: `google-generativeai` (Gemini) and `openai` (OpenAI-compatible). Both are installed always but only loaded at runtime if the relevant env vars are set — no startup cost if unused.

---

### Section 6 — Update Mechanism (Multi-Machine)

A single `brain update` CLI command handles the full upgrade on any machine:

```bash
brain update
```

Sequence:
1. `git pull` in the MemoryBrain repo directory (detected from `MEMORYBRAIN_DIR` env var, set by `brain setup`)
2. Rebuilds Docker: `docker compose up -d --build`
3. Schema migrations run automatically at container startup via `brain/app/migrations.py` — no manual SQL
4. Reinstalls hooks (`~/.claude/hooks/`) and skills (`~/.claude/skills/`) if the repo versions have changed — hash-compared, won't overwrite identical files
5. Prints a changelog of what changed (new git commits since last update)

**Migration system (`brain/migrations/`):**
```
brain/migrations/
├── runner.py              ← runs at FastAPI startup, applies unapplied migrations
└── 001_add_status_supersession.sql   ← first migration (this feature)
```

Each migration file is a plain SQL script. The runner tracks applied migrations in a `schema_migrations` table. Idempotent — safe to run any number of times.

**Why this matters for multi-machine:** Work PC, home PC, and any colleague machine all run:
```bash
cd ~/memorybrain && brain update
```
Docker rebuild applies the new image, migrations run inside the container on startup, hooks and skills update themselves. No manual intervention needed.

---

## Files Changed

| File | Change type | What changes |
|---|---|---|
| `brain/app/storage.py` | Modify | Add status/superseded_by/supersedes columns, migration runner call, tag/type filter queries, archive methods, `include_history` flag, per-project recent state query |
| `brain/app/summarise.py` | Rewrite | Provider abstraction (Ollama/Gemini/OpenAI), caller-provided description bypass |
| `brain/app/ingest_pipeline.py` | Modify | Add supersession scan step post-embed, pre-write |
| `brain/app/search.py` | Modify | Add recency decay, tag/type filters, `include_history` filter |
| `brain/app/mcp/tools.py` | Modify | Update `search_memory`, `add_memory`; add `delete_memory`, update `get_startup_summary` |
| `brain/app/models.py` | Modify | Add `status`, `superseded_by`, `supersedes` to `MemoryEntry`; add `SupersessionThresholds` config |
| `brain/app/main.py` | Modify | Call migration runner at startup |
| `brain/migrations/runner.py` | New | Migration runner — reads migration files, tracks applied, runs unapplied |
| `brain/migrations/001_add_status_supersession.sql` | New | First migration |
| `brain/requirements.txt` | Modify | Add `google-generativeai`, `openai` |
| `.env.example` | Modify | Add Gemini + OpenAI env vars, `RECENCY_DECAY_RATE` |
| `cli/brain.py` | Modify | Add `brain update` command |
| `HOW_IT_WORKS.md` | Modify | Document supersession, new tools, update command, Gemini support |

---

## Version

This feature targets **v0.5.0**. `VERSION` file updated as part of the implementation.

---

## Implementation Notes

- ChromaDB already supports `where={"project": "..."}` metadata filtering — use this for all supersession queries to avoid cross-project false positives.
- Migration runner must execute after `storage.py` initialises the DB connection — order matters in `main.py` lifespan.
- `brain update` fallback: if `MEMORYBRAIN_DIR` env var is not set, the command checks whether the current working directory contains a `brain/` subdirectory. If neither, it prints a clear error with instructions to `cd` to the MemoryBrain repo directory first.

---

## Out of Scope

- Structural diff between old and new memory content (Approach C) — not in this iteration
- Automatic memory expiry / TTL — not needed, supersession handles staleness
- Cross-project supersession detection — memories are scoped to project, cross-project matching is not useful
