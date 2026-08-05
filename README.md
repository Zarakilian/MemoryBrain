<p align="center">
  <img src="docs/assets/memorybrain-logo.jpg" alt="MemoryBrain logo" width="160" height="160" />
</p>

<h1 align="center">MemoryBrain</h1>

<p align="center">
  <strong>Your local multi-AI memory bank</strong><br/>
  Persistent, project-scoped context for Claude, Grok, Codex, Gemini — and anything that speaks MCP or REST.
</p>

<p align="center">
  <a href="https://github.com/Zarakilian/MemoryBrain"><img alt="GitHub" src="https://img.shields.io/badge/github-Zarakilian%2FMemoryBrain-8fb8e8?style=flat-square" /></a>
  <img alt="Version" src="https://img.shields.io/badge/version-2.4.0-ffd98a?style=flat-square" />
  <img alt="MCP tools" src="https://img.shields.io/badge/MCP%20tools-29-7c9cff?style=flat-square" />
  <img alt="Local first" src="https://img.shields.io/badge/local--first-loopback%20only-5ad67d?style=flat-square" />
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-brightgreen?style=flat-square" /></a>
</p>

---

MemoryBrain replaces flat `MEMORY.md` files with a **shared operational memory** every assistant can read and write:

- **FastAPI + SQLite** (FTS5 + sqlite-vec) + Ollama (or Gemini / OpenAI)
- **MCP** over streamable HTTP (`/mcp`), classic SSE (`/sse`), and Docker **stdio**
- **Nebula Atlas UI** at http://localhost:7741/ui
- **Passive store** — assistants save via `add_memory`; no polling of external systems

It is **not** a full Obsidian replacement. It is the brain your AIs share across projects, sessions, and tools. Optional Obsidian export/import bridges the two.

## Why MemoryBrain

| Need | MemoryBrain |
|------|-------------|
| Same context for Grok + Claude + Codex + Gemini | One MCP surface, three transports |
| Survive context-window compaction | Durable SQLite memory + briefs |
| Stop contradictory “facts” | Supersession + conflict edges + verdicts |
| Compress noise over time | Consolidation (“sleep”) → beliefs, decay ranking |
| Start every session oriented | `get_startup_summary` + `get_project_brief` |
| Pin what must never sink | Working-set pins excluded from decay |

## Quick start

**New here?** Start with the full first-run guide: **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**  
(prerequisites, verify steps, multi-AI wiring, backup, troubleshooting).

```bash
git clone https://github.com/Zarakilian/MemoryBrain.git ~/memorybrain
cd ~/memorybrain
# master = MemoryBrain 2.x (there is no separate v2 branch to checkout)
git checkout master
cp .env.example .env
# One command: Docker, models, MCP, hooks, skills
python3 cli/brain.py setup --auto-detect
```

**Requirements:** Docker Desktop (or Compose v2), Git, ~4–8 GB free disk; Python 3.11+ recommended for `brain setup`.

Open **http://localhost:7741/ui** · MCP SSE **http://localhost:7741/sse** · Grok HTTP **http://localhost:7741/mcp**

| Doc | For |
|-----|-----|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | First-time install & verify |
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | Architecture & portable setup |
| [docs/AI_INSTALL_PROMPTS.md](docs/AI_INSTALL_PROMPTS.md) | Let an AI drive install/migrate |
| [docs/CONNECTING_ASSISTANTS.md](docs/CONNECTING_ASSISTANTS.md) | Claude / Grok / Codex / Gemini / REST |

## Architecture (short)

```text
Assistants ──MCP/REST──► MemoryBrain (loopback :7741)
                              │
                     ┌────────┴────────┐
                     │   brain.db      │  FTS5 + vectors + graph + pins
                     │   Ollama/LLM    │  embed · summarise · consolidate
                     └────────┬────────┘
                              ▼
                     Atlas UI  ·  briefs  ·  sleep cycle
```

## Multi-AI doors

| Client | Transport | Config sketch |
|--------|-----------|----------------|
| **Grok** | Streamable HTTP | `url = "http://localhost:7741/mcp"` |
| **Claude Code** | SSE | `claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse` |
| **Codex / Gemini** | stdio | `docker exec -i memorybrain-brain-1 python stdio_server.py` |
| **Any script** | REST | `/project-brief`, `/ingest/note`, `/status`, … |

`GET /status` returns version, tool list, scheduler state, and recommended client configs.

## MCP tools (29)

### Core
`search_memory` · `get_memory` · `add_memory` · `delete_memory` · `get_recent_context` · `list_projects` · `get_startup_summary` · `get_related_memories` · `get_memory_graph` · `consolidate_memory`

### Context bank (v2.2+)
`get_project_brief` · `list_conflicts` · `resolve_conflict` · `dismiss_conflict` · `pin_memory` · `unpin_memory` · `list_pins`

### Ops & orientation (v2.3)
`record_retrieval` · `get_timeline` · `get_entities` · `get_project_policy` · `set_project_policy`

### Synapse — Agent Exchange (v2.4)
`post_task` · `get_agent_inbox` · `reply_to_thread` · `update_task_status` · `list_threads` · `get_thread` · `get_agent_stats`

### Recommended agent protocol

1. `get_startup_summary`
2. `get_agent_inbox(agent=<me>)` — anything the other agents left for you?
3. `get_project_brief(project=…)`
4. Work with typed writes: `fact` / `decision` / `open_loop` / `session` (always pass `source=<me>`)
5. `pin_memory` for env truths and current goals
6. Handoffs: `post_task(kind=review, to_agent=codex, refs=[…])` instead of making the human copy-paste
7. After heavy weeks: `consolidate_memory` → `list_conflicts` → resolve
8. When a search result was *actually used*: `record_retrieval(..., chosen_id=…)`

## What's new

### v2.4.0 — Synapse: the agents talk to each other
- **Agent Exchange** — threads (task/review/question/handoff/discussion) +
  addressed messages between Claude/Grok/Codex/Gemini; pull-based inbox with
  read cursors. 7 new MCP tools, REST twins under `/exchange/*`.
- **⚡ Agents page** (`/ui/agents`) — per-agent totals, per-project share
  donuts, and the Synapse view: agents as neurons, messages as firings.
- Protocol: `skills/agent-exchange/SKILL.md` · design: `docs/AGENT_EXCHANGE.md`.

### v2.3.1 — multi-AI transport clarity + hook hardening
- Session-ingest readiness accepts modern `vector_store` (legacy `chromadb` still works)
- Connecting-assistants docs: streamable HTTP `/mcp` (Grok), SSE (Claude), stdio (Codex)
- CODEX/GROK guides aligned with recommended transports from `/status`

### v2.3.0 — ops, feedback, bridges
- **Nightly light auto-sleep** (`MEMORYBRAIN_AUTO_CONSOLIDATE=true`) — repair, conflicts, loops, decay; optional full LLM beliefs
- **`record_retrieval`** + ranking feedback from chosen results
- **Project brief policy** — Atlas ⚙ policy + MCP get/set
- **Obsidian export/import** — `GET /admin/export/obsidian`, `POST /admin/import/obsidian`
- **Timeline & entity cards** — MCP + REST + `/api/ui/*`
- Professional **logo** for README and Atlas brand

### v2.2.0 — multi-AI context bank
Token-budgeted `get_project_brief`, pins, conflict MCP tools, write policy (`decision` / `open_loop`). See [docs/CONTEXT_BANK_V2.2.md](docs/CONTEXT_BANK_V2.2.md).

### v2.1.0 — the brain that sleeps
Beliefs, `conflicts_with`, strength/decay, Atlas ☾ sleep. Provenance via `derived_from`.

### v2.0.0 — one database + Nebula
sqlite-vec in `brain.db`, automatic graph, local Atlas UI (Stream / Constellation / Chronicle).

## Project detection

1. **`.brainproject`** in the repo root (recommended) — file contains only the slug  
2. Else last meaningful path segment of the working directory  

## Skills

| Skill | What it does |
|-------|----------------|
| `log-everything` | Session summary → MemoryBrain (+ project log suites where configured) |
| `handover` | Full session handover document |
| `map-project-files` | Authoritative file map as a reference memory |
| `agent-exchange` | Multi-AI collaboration protocol: inbox, handoffs, reviews (v2.4) |

## Ops cheatsheet

```bash
# Health
curl -s localhost:7741/health
curl -s localhost:7741/readiness

# Status (add -H "X-Brain-Key: …" when BRAIN_API_KEY is set)
curl -s localhost:7741/status

# Brief
curl -s "localhost:7741/project-brief?project=my-app"

# Export project to Markdown (Obsidian-friendly)
curl -s -X GET "localhost:7741/admin/export/obsidian?project=my-app" \
  -H "X-Brain-Key: $BRAIN_API_KEY"

# Rebuild after git pull
cd ~/memorybrain && git pull && docker compose build brain && docker compose up -d
```

**Never** `docker compose down -v` on a live install — that drops the data volume.

## Configuration (names only)

See [`.env.example`](.env.example). Highlights:

| Variable | Purpose |
|----------|---------|
| `BRAIN_API_KEY` | Protects writes/admin (MCP loopback stays open) |
| `MEMORYBRAIN_AUTO_CONSOLIDATE` | Nightly light sleep |
| `MEMORYBRAIN_RETRIEVAL_FEEDBACK_WEIGHT` | Ranking lift from chosen results |
| `MEMORYBRAIN_VECTOR_BACKEND` | `sqlite_vec` (default) or `chroma` rollback |
| `OLLAMA_*` / `GOOGLE_*` / `OPENAI_*` | Provider selection |

## Docs

| Doc | Contents |
|-----|----------|
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | Architecture & portable setup |
| [docs/CONNECTING_ASSISTANTS.md](docs/CONNECTING_ASSISTANTS.md) | Wire any AI |
| [docs/CONTEXT_BANK_V2.2.md](docs/CONTEXT_BANK_V2.2.md) | Briefs, pins, conflicts |
| [MIGRATION.md](MIGRATION.md) | Upgrades & backups |
| [GROK.md](GROK.md) | Grok-specific ops on this workstation |

## Branch & version policy

| Ref | Meaning |
|-----|---------|
| **`master`** (default) | **Only active branch** — MemoryBrain **2.x** (current: 2.3.1) |
| Tags `v2.x.x` | Releases |
| Old feature branches | Fully merged and removed; do not checkout `feature/memorybrain-2.0` |

```bash
git clone https://github.com/Zarakilian/MemoryBrain.git
git checkout master    # this is MemoryBrain 2
```

## License & philosophy

**MIT License** — free to use, fork, modify, and ship for everyone, including
companies. See [LICENSE](LICENSE).

**Corporations / enterprise:** you may use MemoryBrain under MIT at no cost.
If you want paid support, an SLA, custom features, consulting, or a formal
commercial agreement with the author, that is welcome — see
[COMMERCIAL.md](COMMERCIAL.md). Paid deals are **optional**; they do not replace
the free MIT license unless both parties sign something else.

Local-first, single-user, loopback-bound. Your memories stay on your machine.  
Code is the product; **your data volume is irreplaceable** — back it up.  
**Never** `docker compose down -v` on a machine you care about.

---

<p align="center">
  <img src="docs/assets/memorybrain-logo.jpg" alt="" width="48" height="48" /><br/>
  <sub>MemoryBrain — the brain that sleeps, and wakes up ready for every assistant.</sub>
</p>
