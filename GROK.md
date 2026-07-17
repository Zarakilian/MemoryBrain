# GROK.md — MemoryBrain Operating Guide for Grok

**Last Updated:** 2026-07-17  
**Assistant:** Grok (xAI Grok Build)  
**Project slug:** `memorybrain` (see `.brainproject`)  
**GitHub:** https://github.com/Zarakilian/MemoryBrain  
**Purpose:** Authoritative Grok-facing context for this repo, the live install, multi-AI MCP wiring, and the v2.0 migration.

> Grok does **not** auto-load this file by name. Read it when working on MemoryBrain itself, when debugging MCP/memory, or when resuming migration/ops work.

---

## What MemoryBrain is

Local, **passive**, single-user memory service for AI assistants (Claude Code, Grok, Gemini/Antigravity, Codex, etc.).

- **Stack:** FastAPI + SQLite (FTS5 + **sqlite-vec** in v2) + Ollama (default embeddings/summaries)
- **Transport:** MCP over SSE at `http://localhost:7741/sse` (stdio via Docker wrapper for Gemini)
- **Role:** Stores only what assistants save via `add_memory` / ingest APIs — no polling of external systems
- **Human UI (v2):** MemoryBrain Atlas at http://localhost:7741/ui

It replaces flat `MEMORY.md` files with searchable, project-scoped, durable memory.

---

## Two paths on this machine (important)

| Path | Role |
|------|------|
| `C:\git\_git\MemoryBrain` | **Dev / Grok workspace** — git clone for code, docs, `GROK.md` |
| `C:\Users\Miguel\memorybrain` | **Live runtime install** — Docker Compose project that owns the data volume |

**Do not confuse them.** Runtime containers and the named Docker volume are tied to the live install directory name (`memorybrain` → volumes `memorybrain_brain_data`, `memorybrain_ollama_data`).

| Runtime fact | Value |
|--------------|--------|
| Compose project | `memorybrain` (from live dir) |
| Port | `127.0.0.1:7741` |
| Data volume | `memorybrain_brain_data` → `/app/data` (`brain.db`, legacy `chroma/`) |
| Ollama volume | `memorybrain_ollama_data` |
| MCP SSE | `http://localhost:7741/sse` |
| Status | `GET http://localhost:7741/status` |
| Readiness | `GET http://localhost:7741/readiness` |

**GitHub `master` is now v2.0.0** (merge commit `f2a4a35`, 2026-07-17).  
Historical feature branch: `feature/memorybrain-2.0`. Pre-merge production was **v0.5.0**.

---

## Multi-AI installation status (already installed)

MemoryBrain is already wired for Miguel’s AIs. MCP endpoint is the same for all:

```text
http://localhost:7741/sse
```

### Grok (tight wiring — 2026-07-17)

| Piece | Path / value |
|-------|----------------|
| Config | `C:\Users\Miguel\.grok\config.toml` |
| MCP | `[mcp_servers.memorybrain]` → SSE `http://localhost:7741/sse` |
| MCP auth | `headers = { "X-Brain-Key" = "…" }` (matches live `.env` `BRAIN_API_KEY`) |
| Timeouts | `startup_timeout_sec = 30`, `tool_timeout_sec = 180` |
| Global rule | `C:\Users\Miguel\.grok\rules\memorybrain-session.md` (session protocol for **all** projects) |
| SessionStart hook | `C:\Users\Miguel\.grok\hooks\memorybrain.json` → `scripts\memorybrain-session-start.ps1` |
| PreCompact hook | same JSON → `scripts\memorybrain-precompact.ps1` |
| Hook output | `C:\Users\Miguel\.grok\memorybrain\startup-context.md` + `last-active.txt` |
| Log skill | `C:\Users\Miguel\.grok\skills\log-everything\SKILL.md` (**keep** improved Grok version) |
| Project rules | this repo’s `AGENTS.md` + `GROK.md` |

**Restart Grok** after MCP/header changes so SSE reconnects with the API key.

### Claude Code

- Session-start: `~/.claude/hooks/session-start-memory-wrapper.sh` (loads `BRAIN_API_KEY` from live `.env`, then real hook)
- Real hook: `session-start-memory.sh` (`MEMORYBRAIN_DIR=/c/Users/Miguel/memorybrain`)
- Pre-compact: `pre-compact-auto-handover.py` / `pre-compact-ingest.py` (auto-load key from live `.env`)
- Session protocol: `C:\Users\Miguel\.claude\Claude.md` / `CLAUDE.md`  
  (timestamp → `get_startup_summary` → `get_recent_context` → stop)
- Skills under `C:\Users\Miguel\.claude\skills\` (log-everything, handover, map-project-files)

### Codex (OpenAI)

- **Transport:** stdio (not SSE) — `docker exec -i memorybrain-brain-1 python stdio_server.py`
- Config: `C:\Users\Miguel\.codex\config.toml` → `[mcp_servers.memorybrain]`
- Global protocol: `C:\Users\Miguel\.codex\AGENTS.md`
- Skill: `C:\Users\Miguel\.codex\skills\log-everything\SKILL.md`
- Project guide: [`CODEX.md`](CODEX.md)
- Verify: `codex mcp get memorybrain` → `transport: stdio`
- Restart Codex after config changes. HTTP fallback still needs `X-Brain-Key` when set.

### Gemini / Antigravity

- MCP tool descriptors: `C:\Users\Miguel\.gemini\antigravity\mcp\memorybrain\`  
  (and antigravity-ide twin)

**After container rebuilds:** restart Grok/Claude MCP clients if SSE handshake fails; the service may still be healthy on HTTP while clients hold a stale stream.

---

## Session-start protocol (all agents)

1. Check auto-loaded `MEMORY.md` for `**MemoryBrain Last Active:** <timestamp>`
2. If **&lt; 7 days old** → MemoryBrain ACTIVE:
   - Call `get_startup_summary`
   - Call `get_recent_context` with `days=14` (and project slug when known)
   - **Stop** reading project handover files unless the user asks
3. If missing/stale or MCP down → fall back to project `CLAUDE_HANDOVERS/MEMORY.md` / latest `HANDOVER-*.md`

Project slug resolution:

1. `.brainproject` in workspace root (preferred)
2. Else last meaningful path segment (skip `mnt`, `c`, `git`, `_git`, empty)

Known project slugs in this corpus (pre-migration sample):  
`baby-bee-blossom`, `insect-cursor`, `baby-bee-blossom-storefront`, `_git`, `yoohoo-blogs`, `system`, `personal`, …

---

## MCP tools

### Unchanged (v0.5 → v2)

| Tool | Purpose |
|------|---------|
| `search_memory` | Hybrid keyword + semantic search |
| `get_memory` | Full content by id |
| `add_memory` | Store note/fact/session/… |
| `get_recent_context` | Recent entries by project |
| `list_projects` | Projects + last activity |
| `get_startup_summary` | Compact session injection |
| `delete_memory` | Hard delete by id |

### New in v2.0

| Tool | Purpose |
|------|---------|
| `get_related_memories` | Graph neighbours with link-kind explanations |
| `get_memory_graph` | Full node/edge payload for a project (or global) |

### HTTP fallbacks (when MCP handshake fails)

| Method | Path | Use |
|--------|------|-----|
| GET | `/status` | Version + project_count |
| GET | `/readiness` | Subsystem checks (`ready`, `vector_store`, ollama, …) |
| POST | `/ingest/session` | Session summary body `{ project, source, content }` |
| POST | `/ingest/note` | Short notes + tags |
| POST | `/admin/rebuild-graph` | One-time / repair graph build |
| POST | `/admin/backfill-vectors` | Re-embed gaps (provider must be up) |
| GET | `/ui` | Atlas UI |
| GET | `/ui/doctor` | In-browser diagnostics |
| GET | `/api/ui/version` | Build stamp |

If `BRAIN_API_KEY` is set, write/admin paths need `X-Brain-Key`. Loopback binding remains the main trust boundary for read UI.

---

## v0.5.x → v2.0.0 migration (this machine)

Authoritative guide in-repo: [`MIGRATION.md`](MIGRATION.md). AI-strict steps: [`docs/AI_INSTALL_PROMPTS.md`](docs/AI_INSTALL_PROMPTS.md).

### What changed

| Area | v0.5.x | v2.0.0 |
|------|--------|--------|
| Vectors | Chroma under `/app/data/chroma/` | `vec_memories` in `brain.db` (sqlite-vec) |
| Graph | none | semantic / tag / reference / session_chain edges |
| MCP tools | 7 | 9 |
| UI | none | Atlas at `/ui` |
| Data files | db + chroma | db primary; chroma kept for rollback |

### Backup location (mandatory, done 2026-07-17)

```text
C:\Users\Miguel\memorybrain-backups\2026-07-17_080847_pre-v2\
  brain-backup-20260717.tar.gz   # full Docker volume tar (~1.3MB)
  .env.backup
  memory-counts.txt              # MEMORY_COUNT_BEFORE = 309
  status.json                    # version 0.5.0, project_count 8
  skills\                        # repo skills snapshot
  grok-log-everything\           # improved Grok skill snapshot
```

Also: live dir `C:\Users\Miguel\memorybrain\.env.backup-20260717`.

### Pre-migration counts (verify after upgrade)

```text
memories: 309
projects: 8
  baby-bee-blossom           256
  insect-cursor               24
  baby-bee-blossom-storefront 18
  _git                         6
  babybeeblossom               2
  yoohoo-blogs                 1
  system                       1
  personal                     1
```

### Upgrade commands used (live install)

```powershell
cd C:\Users\Miguel\memorybrain
git fetch origin feature/memorybrain-2.0
git checkout -B feature/memorybrain-2.0 origin/feature/memorybrain-2.0
# .env preserved; v2 defaults appended if missing
docker compose build brain
docker compose up -d
# then: readiness, memory/vector counts, POST /admin/rebuild-graph
```

### Rollback options (do not invent others)

1. **Keep v2 code, old vectors:** set `MEMORYBRAIN_VECTOR_BACKEND=chroma` in `.env`, restart.
2. **Full code rollback:** checkout previous commit/tag on live install, `docker compose build && up -d`. New tables are ignored by old code.
3. **Restore volume from tar** only if something went badly wrong (stop brain, extract tar into volume carefully).

Never: `docker compose down -v`, volume prune, or deleting `chroma/` until long after v2 is trusted (and only after another backup).

---

## Grok improved `log-everything` skill (keep)

**Canonical Grok skill path (do not overwrite with stock Claude skill):**

```text
C:\Users\Miguel\.grok\skills\log-everything\SKILL.md
```

Repo stock skill (`skills/log-everything/SKILL.md`) is the simpler Claude-oriented version. Grok’s version is **strictly better** for this workstation and must be preserved across MemoryBrain upgrades.

### Grok skill capabilities (summary)

1. Project detection via `.brainproject` + BBB special-case
2. 300–700 word structured session summary (decisions, files, SHAs, deploys)
3. MemoryBrain via MCP **or HTTP fallback** (`/ingest/session`, `/ingest/note`)
4. Next-session notes prompt
5. **Baby Bee Blossom multi-file suite** when in that project
6. Secret-safe logging rules
7. Confirm checklist to the user

Improvements over stock: Grok Files/ narrative logs, correct BBB handoff paths, HTTP fallback when MCP is down, deploy/SHA capture, dedup-aware DEV_LOG, works outside BBB as a light MemoryBrain logger.

When updating MemoryBrain’s bundled skills for Claude/Gemini, **do not replace** the Grok skill with the stock file. Optionally re-copy Grok improvements into other agents later deliberately.

---

## Day-to-day ops

```powershell
# Status
Invoke-RestMethod http://localhost:7741/status
Invoke-RestMethod http://localhost:7741/readiness

# Logs
docker compose -f C:\Users\Miguel\memorybrain\docker-compose.yml logs -f brain

# Rebuild after code pull (live install)
cd C:\Users\Miguel\memorybrain
git pull
docker compose build brain
docker compose up -d

# One-shot volume backup
docker compose -f C:\Users\Miguel\memorybrain\docker-compose.yml stop brain
docker run --rm -v memorybrain_brain_data:/data -v "${PWD}:/backup" alpine `
  tar czf "/backup/brain-backup-$(Get-Date -Format yyyyMMdd).tar.gz" /data
docker compose -f C:\Users\Miguel\memorybrain\docker-compose.yml start brain
```

### Dev workspace (`C:\git\_git\MemoryBrain`)

- Branch: `feature/memorybrain-2.0`
- Use for reading code, editing docs, PRs — **not** for a second compose stack unless you force project name `-p memorybrain` and understand volume sharing risks
- Prefer implementing against a PR, then pull into the **live** install directory

---

## Env vars (names only — never commit secrets)

| Variable | Notes |
|----------|--------|
| `BRAIN_PORT` | Default 7741 |
| `OLLAMA_URL` | In compose: `http://ollama:11434` |
| `BRAIN_API_KEY` | Optional; empty = open on loopback |
| `RECENCY_DECAY_RATE` | Search recency bias |
| `MEMORYBRAIN_VECTOR_BACKEND` | `sqlite_vec` (default) or `chroma` rollback |
| `MEMORYBRAIN_GRAPH_ENABLED` | `true` / `false` |
| `OLLAMA_*` / `GOOGLE_*` / `OPENAI_*` | Provider models — see `.env.example` |

Live `.env` lives only under `C:\Users\Miguel\memorybrain\.env` (gitignored).

---

## Key docs in this repo

| File | Why |
|------|-----|
| `README.md` | Product overview, quick start, v2 highlights |
| `MIGRATION.md` | v0.5 → v2 upgrade + rollback |
| `HOW_IT_WORKS.md` | Architecture, hooks, portable setup |
| `docs/AI_INSTALL_PROMPTS.md` | Strict AI-driven install/migrate scripts |
| `docs/GEMINI_SETUP_GUIDE.md` | Gemini/stdio specifics |
| `PROGRESS_LOG.md` | Historical build log (may lag VERSION) |
| `VERSION` | Package version string |
| `GROK.md` | **This file** — Grok ops + machine layout |

---

## Gotchas for the next agent

1. **Live data is in Docker volume `memorybrain_brain_data`**, not in the git working tree.
2. **Grok MCP** may fail handshake while HTTP `/status` is fine — use HTTP fallbacks; restart client if needed.
3. **Ingest can take 30–120s** (embed + summarise); retry once, don’t spin forever.
4. **Never** `docker compose down -v` on the live project.
5. Keep **Grok log-everything** at `~\.grok\skills\log-everything\` — do not clobber with stock skill from this repo.
6. After migration, if `vectors` ≪ `memories`, run `POST /admin/backfill-vectors` with Ollama healthy.
7. Graph edges for the *existing* corpus need `POST /admin/rebuild-graph` once after upgrade (new ingests link automatically).

---

## Current migration session goals (2026-07-17)

- [x] Clone repo to `C:\git\_git\MemoryBrain`
- [x] Full pre-v2 backup of volume + env + skill snapshots
- [x] Checkout `feature/memorybrain-2.0` on live install
- [x] Build/recreate brain container
- [x] Confirm readiness + memory count == 309 + vectors == 309
- [x] Rebuild graph (`total_edges`: 1692; semantic 1141, tag 299, session_chain 131, reference 121)
- [x] Write this `GROK.md`
- [x] Preserve improved Grok log-everything (live path + backup + `skills/log-everything/SKILL_GROK.md`)

### Post-migration verification (recorded)

| Check | Result |
|-------|--------|
| `version` | `2.0.0` |
| `/readiness` | `ready: true`, `vector_store: ok`, ollama/embed/summary ok |
| memories | **309** at migrate; **310** after auth smoke test |
| vectors | equal to memories |
| projects | 8 (same breakdown) |
| graph rebuild | 255 memories linked, 1692 edges |
| Atlas UI | HTTP 200 at `/ui` |
| build stamp | `19dc0c02c640.20260717-0610` (rebuilds change stamp) |
| GitHub | `master` @ `f2a4a35` merge 2.0 |

### Auth note

`BRAIN_API_KEY` is set on the live install. All write/admin (and currently status) paths need `X-Brain-Key`.  
Clients that must send it: Grok MCP headers, Grok hooks (read `.env`), Claude wrapper + pre-compact loaders.  
**Never commit the key.** Rotate by editing live `.env`, restarting brain, and updating Grok `config.toml` headers.
