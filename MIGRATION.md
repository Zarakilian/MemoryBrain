# Migrating MemoryBrain v0.5.x → v2.0.0

> **Upgrading a 2.x brain to 3.0?** Use [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md)
> (`python3 cli/brain.py upgrade` does the backup, rebuild and count check).
> This page covers the older upgrades.

This guide is for anyone running MemoryBrain v0.5.x who wants to upgrade to
v2.0.0.

**Backups always go outside the repo** (`~/memorybrain-backups`) and are taken
with the brain stopped: a copy of a live WAL database can be torn, and brain
data must never sit in a git folder.

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
   mkdir -p ~/memorybrain-backups
   docker compose stop brain
   docker run --rm -v memorybrain_brain_data:/data:ro -v ~/memorybrain-backups:/backup alpine \
       tar czf /backup/brain-backup-$(date +%Y%m%d).tar.gz -C /data .
   cp .env ~/memorybrain-backups/env-backup-$(date +%Y%m%d)
   docker compose start brain
   ```

   Windows PowerShell:

   ```powershell
   New-Item -ItemType Directory -Force "$HOME\memorybrain-backups" | Out-Null
   docker compose stop brain
   docker run --rm -v memorybrain_brain_data:/data:ro -v "$HOME\memorybrain-backups:/backup" alpine tar czf /backup/brain-backup-$(Get-Date -Format yyyyMMdd).tar.gz -C /data .
   Copy-Item .env "$HOME\memorybrain-backups\env-backup-$(Get-Date -Format yyyyMMdd)"
   docker compose start brain
   ```

   Verify the `.tar.gz` in `~/memorybrain-backups` exists and isn't tiny
   before continuing. If your volume has a different name, find it with
   `docker volume ls`.

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
curl -s -X POST -H "X-Brain-Client: curl" localhost:7741/admin/rebuild-graph | python3 -m json.tool

# 4. Open the UI:
#    http://localhost:7741/ui
```

**If `vectors` < `memories`:** a few embeddings were missing from Chroma
(usually memories written during a past crash). Re-embed them via your
provider: `curl -X POST -H "X-Brain-Client: curl" localhost:7741/admin/backfill-vectors`. Requires
Ollama (or your configured provider) to be up.

**If you set `BRAIN_API_KEY`:** the admin endpoints require the
`X-Brain-Key` header. The UI's read paths (`/ui`, `/api/ui/*`, `/static`)
deliberately do not — the loopback-only port binding is the trust
boundary, exactly as it already is for `/sse`. The UI's *write* surface
(`/api/ui/edit/*`) is the exception: it enforces the key exactly like
`/ingest/*`; the UI prompts for it once and remembers it per browser.

## Rollback

Two independent levels, both non-destructive:

- **Keep v2 code, use the old vector store:** set
  `MEMORYBRAIN_VECTOR_BACKEND=chroma` in `.env` and restart. Semantic search
  reads your untouched Chroma directory again. (Memories added while on
  sqlite_vec won't have Chroma embeddings — their keyword search still works.)
- **Full rollback:** `git checkout v0.5.0 && docker compose build && docker
  compose up -d`. The new tables (`vec_memories`, `memory_links`, `tag_stats`)
  and two new columns are simply ignored by the old code. Restore the tar
  backup only if something went badly wrong: with the brain stopped,
  `docker run --rm -v memorybrain_brain_data:/data -v ~/memorybrain-backups:/backup alpine sh -c "rm -f /data/brain.db /data/brain.db-wal /data/brain.db-shm && tar xzf /backup/<your-backup>.tar.gz -C /data"`.

Once you've run happily on v2 for a while, you may delete the legacy
directory to reclaim disk: `docker compose exec brain rm -rf /app/data/chroma`
— after a backup, and understanding it removes the chroma rollback path.

## New in your toolbox after migrating

- MCP: `get_related_memories(memory_id, ...)` — graph neighbours with
  per-kind explanations; `get_memory_graph(project?, ...)` — full node/edge
  payload.
- HTTP: `POST /admin/rebuild-graph`, `POST /admin/backfill-vectors`,
  `GET /api/ui/graph`, `GET /api/ui/search`, `GET /api/ui/stats`.
- Web UI — the **MemoryBrain Nebula**: one living world at `/ui` with a
  project rail, a command palette (`Ctrl+K`), a sliding inspector, and three
  lenses on the same data — **Stream** (server-rendered daily feed; works with
  JS disabled), **Constellation** (the memories as luminous stars inside a
  full-screen living nebula scene; remembered 2D switch, automatic 2D
  fallback), and **Chronicle** (horizontal time axis of sessions/handovers
  per project, drawn from the `session_chain` edges). Switch lenses with
  `1/2/3`. World-ambience and cursor-familiar toggles live in the rail.
  Old `/ui/graph` and `/ui/project/{slug}` URLs redirect.
- Editing from the Nebula: add notes/facts/references (or upload a ≤1 MB
  text file), create/edit projects, and Edit / Archive / Delete from the
  inspector. Guardrails: archive is the reversible default; hard delete
  requires typing the memory id's first 8 characters; a project cannot be
  deleted while it still holds memories.
- UI diagnostics: `GET /api/ui/version` (build stamp baked at image build,
  also in the footer and asset URLs) and `/ui/doctor` (dependency-free
  in-browser checks with a copy-paste report).

## Env vars added in v2.0.0

| Var | Default | Purpose |
|---|---|---|
| `MEMORYBRAIN_VECTOR_BACKEND` | `sqlite_vec` | `chroma` = legacy rollback |
| `MEMORYBRAIN_GRAPH_ENABLED` | `true` | `false` disables the linker |


## v2.1.0 — the consolidation cycle

Migration `004_consolidation.sql` applies automatically at startup: adds
`strength` (default 1.0) and `last_recalled` to `memories`, and rebuilds
`memory_links` to accept the new `derived_from` and `conflicts_with` edge
kinds. No action needed; fully backward compatible — existing memories
start at strength 1.0 and behave exactly as before until a consolidation
run or a recall touches them.

## v2.5.0 — the workspace layer (2.4 → 2.5, any machine)

Additive only. No data is touched; five new tables and three `projects` columns.

1. `git pull` on `master`, then `docker compose up -d --build`. The container start applies `008_workspace.sql`. 2.5.0 also ships the Files lens and file crystals in the Nebula; hard-reload the Atlas once after the rebuild (Ctrl+Shift+R) so the new scripts load.
2. Reinstall the hooks and skills, because `hooks/session-ingest.sh` and `skills/map-project-files` changed: `python cli/brain.py setup --auto-detect` (idempotent), or copy `hooks/session-ingest.sh` to `~/.claude/hooks/session-start-memory.sh` and `skills/map-project-files/SKILL.md` to `~/.claude/skills/map-project-files/SKILL.md` by hand.
3. Keep `.brainproject` markers out of every git repo on that machine. `brain scan` writes a marker into each bound folder, including repo clones. Add the marker to your global git excludes once:
   ```
   git config --global core.excludesFile ~/.config/git/ignore
   echo .brainproject >> ~/.config/git/ignore
   ```
4. `python cli/brain.py scan --root "C:\work\repos" --label git --init`, edit `workspace-map.proposed.json`, then `python cli/brain.py scan --apply workspace-map.proposed.json`, then `python cli/brain.py scan`. Once per machine; markers already sitting in your folders are honoured. Put bulk folders you do not want indexed in `~/.memorybrain/scan-ignore`, one glob per line (for example `vendor/*`, `archive/*`, `*/generated/*`).
5. `curl -X POST -H "X-Brain-Client: curl" http://localhost:7741/admin/rebuild-file-links` once, so existing memories get their `file_ref` edges.
6. `curl -X POST -H "X-Brain-Client: curl" http://localhost:7741/admin/backfill-project-descriptions` once, or wait for the next light sleep.
7. Run `python cli/brain.py scan --full` now and then (weekly is plenty). Only a full scan marks files deleted or moved; the everyday incremental scan sends changes only.

One brain per machine. A full scan from a second PC using the same `root` label will mark files that exist only on the first machine as deleted.

Rollback: run the 2.4.0 image again. It ignores the new tables and columns.
