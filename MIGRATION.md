# Migrating MemoryBrain v0.5.x → v2.0.0

This guide is for anyone running MemoryBrain v0.5.x who wants to upgrade to
v2.0.0.

> **Letting an AI assistant do this for you?** Use the strict, model-agnostic
> prompts in [docs/AI_INSTALL_PROMPTS.md](docs/AI_INSTALL_PROMPTS.md) — they
> work with any assistant (Claude, Gemini, ChatGPT, local models) and bake in
> the backup-first, stop-on-mismatch discipline this guide expects. The upgrade is designed to be **zero-effort and zero-data-loss**: the
migration runs automatically the first time the new container starts, and your
old data is never modified.

## What v2.0.0 changes

| Area | v0.5.x | v2.0.0 |
|---|---|---|
| Vector store | embedded ChromaDB (`/app/data/chroma/`) | `vec_memories` table inside `brain.db` (sqlite-vec) |
| Memory graph | none | automatic edges: semantic, tag, reference, session_chain |
| MCP tools | 7 | 9 (adds `get_related_memories`, `get_memory_graph`) |
| Human interface | none | web UI at `http://localhost:7741/ui` |
| Data files | `brain.db` + Chroma directory | `brain.db` only (Chroma dir kept as rollback) |

Nothing about the session hooks, the MCP endpoint (`/sse`), the 7 existing
tool contracts, or the Ollama/Gemini/OpenAI provider setup changes. Your
Claude Code / Gemini configuration keeps working as-is.

## Before you start (2 minutes)

1. **Back up your data volume and config.** Non-negotiable, takes seconds:

   ```bash
   docker compose stop brain
   docker run --rm -v memorybrain_brain_data:/data -v "$PWD":/backup alpine \
       tar czf /backup/brain-backup-$(date +%Y%m%d).tar.gz /data
   cp .env .env.backup-$(date +%Y%m%d)
   docker compose start brain
   ```

   Windows PowerShell:

   ```powershell
   docker compose stop brain
   docker run --rm -v memorybrain_brain_data:/data -v "${PWD}:/backup" alpine tar czf /backup/brain-backup-$(Get-Date -Format yyyyMMdd).tar.gz /data
   Copy-Item .env ".env.backup-$(Get-Date -Format yyyyMMdd)"
   docker compose start brain
   ```

   Verify the `.tar.gz` exists and isn't tiny before continuing. If your
   volume has a different name, find it with `docker volume ls`.

2. Note your memory count — you'll verify it after migration:
   open a session and ask the assistant to `list_projects`, or:
   ```bash
   docker compose exec brain python -c "
   import sqlite3; print(sqlite3.connect('/app/data/brain.db').execute(
       'SELECT COUNT(*) FROM memories').fetchone()[0])"
   ```

## Upgrade (one command, ~2 minutes)

```bash
git pull                      # or: git checkout v2.0.0
docker compose build brain
docker compose up -d
```

That's it. On first startup the app automatically:

1. Applies schema migrations `002_vec_memories.sql` and `003_graph.sql`
   (additive only — no existing column or table is altered destructively).
2. **Copies every embedding out of your Chroma directory into `brain.db`**
   (idempotent; a no-op on every later startup). Your Chroma directory is
   opened read-only and left fully intact.

## Verify (1 minute)

```bash
# 1. All subsystems green?
curl -s localhost:7741/readiness | python3 -m json.tool
#    expect: "ready": true, "vector_store": "ok"

# 2. Memory count unchanged?  (compare with the number you noted)
docker compose exec brain python -c "
import sqlite3
c = sqlite3.connect('/app/data/brain.db')
print('memories:', c.execute('SELECT COUNT(*) FROM memories').fetchone()[0])
print('vectors: ', c.execute('SELECT COUNT(*) FROM vec_memories').fetchone()[0])"
#    vectors should equal (or be within a few of) memories — see gap note below

# 3. Link your existing corpus into the graph (one-time, a few seconds
#    per thousand memories):
curl -s -X POST localhost:7741/admin/rebuild-graph | python3 -m json.tool

# 4. Open the UI:
#    http://localhost:7741/ui
```

**If `vectors` < `memories`:** a few embeddings were missing from Chroma
(usually memories written during a past crash). Re-embed them via your
provider: `curl -X POST localhost:7741/admin/backfill-vectors`. Requires
Ollama (or your configured provider) to be up.

**If you set `BRAIN_API_KEY`:** the admin endpoints require the
`X-Brain-Key` header. The read-only UI (`/ui`, `/api/ui/*`, `/static`)
deliberately does not — the loopback-only port binding is the trust
boundary, exactly as it already is for `/sse`.

## Rollback

Two independent levels, both non-destructive:

- **Keep v2 code, use the old vector store:** set
  `MEMORYBRAIN_VECTOR_BACKEND=chroma` in `.env` and restart. Semantic search
  reads your untouched Chroma directory again. (Memories added while on
  sqlite_vec won't have Chroma embeddings — their keyword search still works.)
- **Full rollback:** `git checkout v0.5.0 && docker compose build && docker
  compose up -d`. The new tables (`vec_memories`, `memory_links`, `tag_stats`)
  and two new columns are simply ignored by the old code. Restore the tar
  backup only if something went badly wrong.

Once you've run happily on v2 for a while, you may delete the legacy
directory to reclaim disk: `docker compose exec brain rm -rf /app/data/chroma`
— after a backup, and understanding it removes the chroma rollback path.

## New in your toolbox after migrating

- MCP: `get_related_memories(memory_id, ...)` — graph neighbours with
  per-kind explanations; `get_memory_graph(project?, ...)` — full node/edge
  payload.
- HTTP: `POST /admin/rebuild-graph`, `POST /admin/backfill-vectors`,
  `GET /api/ui/graph`, `GET /api/ui/search`, `GET /api/ui/stats`.
- Web UI — **MemoryBrain Atlas**: one continuous workspace at `/ui` with a
  project rail, a command palette (`Ctrl+K`), a sliding inspector, and three
  lenses on the same data — **Stream** (server-rendered daily feed; works with
  JS disabled), **Constellation** (3D orbit view of the memory web by
  default, remembered 2D switch, automatic 2D fallback), and **Chronicle**
  (horizontal time axis of sessions/handovers per project, drawn from the
  `session_chain` edges). Switch lenses with `1/2/3`. Parchment theme and
  codex-margin ambience toggles live in the rail. Old `/ui/graph` and
  `/ui/project/{slug}` URLs redirect.
- UI diagnostics: `GET /api/ui/version` (build stamp baked at image build,
  also in the footer and asset URLs) and `/ui/doctor` (dependency-free
  in-browser checks with a copy-paste report).

## Env vars added in v2.0.0

| Var | Default | Purpose |
|---|---|---|
| `MEMORYBRAIN_VECTOR_BACKEND` | `sqlite_vec` | `chroma` = legacy rollback |
| `MEMORYBRAIN_GRAPH_ENABLED` | `true` | `false` disables the linker |
