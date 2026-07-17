# MemoryBrain — Agent Rules (Grok / multi-AI)

**Project slug:** `memorybrain` (see `.brainproject`)  
**Live runtime:** `C:\Users\Miguel\memorybrain` (Docker volumes) — this git workspace is the **dev clone**.  
**Full Grok guide:** read [`GROK.md`](GROK.md) for layout, migration, MCP, rollback, and ops.

## Non-negotiable session start

1. Prefer **MemoryBrain MCP** (`memorybrain` server) when available.
2. Call in order: `get_startup_summary` → `get_recent_context` (project=`memorybrain` or current workspace slug).
3. If MCP handshake fails: use HTTP `http://localhost:7741` (`/status`, `/readiness`, `/startup-summary`, `/next-session`, `/ingest/*`).
4. Do **not** load stale `PROGRESS_LOG.md` as primary memory when Brain is healthy.
5. **Never log secrets** (`.env`, `BRAIN_API_KEY`, tokens).

## Data safety

- Data lives in Docker volume `memorybrain_brain_data`, not in this git tree.
- Never `docker compose down -v` or prune that volume.
- Before risky upgrades: tar the volume (see `MIGRATION.md` / `GROK.md`).
- Keep Grok’s improved skill at `~/.grok/skills/log-everything/SKILL.md` — do not overwrite with stock `skills/log-everything/SKILL.md`. Grok reference copy: `skills/log-everything/SKILL_GROK.md`.

## When changing this service

- Edit here (or PR), then pull into the **live** install dir and `docker compose build brain && docker compose up -d`.
- Verify: `/readiness` ready, memory count unchanged after migrations, Atlas at `/ui`.
- New MCP tools in v2: `get_related_memories`, `get_memory_graph`.

## End of session

Use Grok skill **log-everything** (or stock Claude `/log-everything`) so sessions land in MemoryBrain.
