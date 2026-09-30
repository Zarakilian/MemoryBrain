# MemoryBrain — Agent Rules (Grok / multi-AI)

**Project slug:** `memorybrain` (see `.brainproject`)  
**Live runtime:** the Docker Compose install that owns the data volume. This git workspace is either that install or a separate dev clone. Where each lives on a given machine belongs in that machine's `.local/` notes, never in git.  
**Wiring each assistant:** [`docs/CONNECTING_ASSISTANTS.md`](docs/CONNECTING_ASSISTANTS.md) covers Grok, Claude, Codex and Gemini.

## Repo rule: application only

This repo holds only the MemoryBrain application. Never commit brain data, backups, secrets, or anything from a specific machine: machine names, user paths, hostnames, project or work details, memory ids. Keep machine notes in `.local/`, which git ignores. The hooks enforce this, so enable them once per clone:

```bash
git config core.hooksPath .githooks
```

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
- Before risky upgrades: tar the volume into a folder outside the repo (see `MIGRATION.md`).
- If you keep the Grok version of the log skill (`skills/log-everything/SKILL_GROK.md`) in `~/.grok/skills/`, do not overwrite it with the stock `skills/log-everything/SKILL.md`.

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
- When the user says where a project or file lives on disk, call `set_project_info(project, home_path=…)`. Never ask them to map folders manually.
- **Tag + pin trail** (companion, not replacement): for searchable Atlas history
  and *standing* constraints, also use tags `ai-assist-request` /
  `ai-assist-response`, `from:<me>`, `for:<target>`, `status:open|done`, and
  pin open work. Archive + unpin when done. Synapse = conversation; memories =
  conclusions.

## End of session

Use Grok skill **log-everything** (or stock Claude `/log-everything`) so sessions land in MemoryBrain.
If you finished (or started) work another agent should pick up, post or reply to the exchange thread before ending.
