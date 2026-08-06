# MemoryBrain — Agent Rules (Grok / multi-AI)

**Project slug:** `memorybrain` (see `.brainproject`)  
**Live runtime:** `C:\Users\Miguel\memorybrain` (Docker volumes) — this git workspace is the **dev clone**.  
**Full Grok guide:** read [`GROK.md`](GROK.md) for layout, migration, MCP, rollback, and ops.

## Non-negotiable session start

1. Prefer **MemoryBrain MCP** (`memorybrain` server) when available.
2. Call in order: `get_startup_summary` → `get_agent_inbox(agent="<me>")` →
   `get_recent_context` (project=`memorybrain` or current workspace slug).
   Identify as exactly one of `claude` / `grok` / `codex` / `gemini`.
   If the inbox has threads, surface them to the user before starting work.
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
- New MCP tools in v2: `get_related_memories`, `get_memory_graph`; v2.1 adds
  `consolidate_memory` (run the consolidation cycle — beliefs, conflicts,
  open loops, decay).

## Agent-to-agent collaboration (Synapse, v2.4)

- Other agents may hand you work via exchange threads — that's what the inbox
  call surfaces. Full protocol: `skills/agent-exchange/SKILL.md`,
  design: `docs/AGENT_EXCHANGE.md`, dual-channel guide: `docs/CROSS_AI_ASSIST.md`.
- Handing off: save substance with `add_memory` first, then
  `post_task(kind="review"/"task"/…, to_agent="codex"/…, refs=[memory ids])`.
  Refs over blobs — never paste whole files into thread bodies.
- Always pass `source="<me>"` on `add_memory` so the ⚡ Agents analytics
  (`/ui/agents`) attribute your work correctly.
- **Tag + pin trail** (companion, not replacement): for searchable Atlas history
  and *standing* constraints, also use tags `ai-assist-request` /
  `ai-assist-response`, `from:<me>`, `for:<target>`, `status:open|done`, and
  pin open work. Archive + unpin when done. Synapse = conversation; memories =
  conclusions.

## End of session

Use Grok skill **log-everything** (or stock Claude `/log-everything`) so sessions land in MemoryBrain.
If you finished (or started) work another agent should pick up, post or reply to the exchange thread before ending.
