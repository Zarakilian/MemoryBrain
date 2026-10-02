<p align="center">
  <img src="docs/assets/memorybrain-logo.jpg" alt="MemoryBrain logo" width="160" height="160" />
</p>

<h1 align="center">MemoryBrain</h1>

<p align="center">
  <strong>Your local multi-AI memory bank</strong><br/>
  Persistent, project-scoped context for Claude, Grok, Codex, Gemini, and anything that speaks MCP or REST.
</p>

<p align="center">
  <a href="https://github.com/Zarakilian/MemoryBrain"><img alt="GitHub" src="https://img.shields.io/badge/github-Zarakilian%2FMemoryBrain-8fb8e8?style=flat-square" /></a>
  <img alt="Version" src="https://img.shields.io/badge/version-3.2.1-ffd98a?style=flat-square" />
  <img alt="MCP tools" src="https://img.shields.io/badge/MCP%20tools-15%20core%20%C2%B7%2035-7c9cff?style=flat-square" />
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
| Know which files a memory came from | Workspace index + `file_ref` edges (`brain scan`) |
| See which files a memory came from, in the map | Files as crystals in the Nebula, a Files lens, a file page |
| Learn how you want things done | Your corrections become rules, once you confirm them |
| Know who wrote what, and how far to trust it | Writer and trust on every memory; briefs labelled as data |
| Find the middle of a long handover | Chunked vectors, fused keyword and vector ranking, `as_of` history |
| Never lose a write | Store first, WAL, atomic migrations with backups, archive instead of delete |

## Quick start

**New here?** Start with the full first-run guide: **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**  
(prerequisites, verify steps, multi-AI wiring, backup, troubleshooting).

```bash
git clone https://github.com/Zarakilian/MemoryBrain.git ~/memorybrain
cd ~/memorybrain
cp .env.example .env
# One command: Docker, models, MCP, hooks, skills
python3 cli/brain.py setup --auto-detect
```

**Already running MemoryBrain?** Upgrade in about 15 minutes with
[docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md), or paste
[Prompt 3](docs/AI_INSTALL_PROMPTS.md#prompt-3-upgrade-an-existing-install-to-3x)
into an AI assistant and let it drive. `python3 cli/brain.py upgrade` backs up,
rebuilds and checks the memory count in one step.

**Requirements:** Docker Desktop (or Compose v2), Git, ~4–8 GB free disk; Python 3.11+ recommended for `brain setup`.

Open **http://localhost:7741/ui** · MCP SSE **http://localhost:7741/sse** · Grok HTTP **http://localhost:7741/mcp**

| Doc | For |
|-----|-----|
| [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) | First-time install & verify |
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | Architecture & portable setup |
| [docs/AI_INSTALL_PROMPTS.md](docs/AI_INSTALL_PROMPTS.md) | Let an AI drive an install or an upgrade |
| [docs/CONNECTING_ASSISTANTS.md](docs/CONNECTING_ASSISTANTS.md) | Claude / Grok / Codex / Gemini / REST |
| [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md) | Upgrade an existing install to the newest 3.x |
| [CHANGELOG.md](CHANGELOG.md) | Every change, breaking ones first |

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

## MCP tools (version 3: 15 core, 35 in all)

An agent sees 15 core tools by default. `brain_admin(action, args)` runs the
rest with the same arguments; `MEMORYBRAIN_TOOLS=full` lists every tool again.

| Group | Core tools |
|---|---|
| Read and write | `search_memory` · `get_memory` · `add_memory` |
| Session start | `get_project_brief` · `get_startup_summary` · `get_recent_context` |
| Projects and files | `pin_memory` · `set_project_info` · `get_file_context` |
| Other agents | `get_agent_inbox` · `post_task` · `reply_to_thread` · `get_thread` |
| Learning and the rest | `record_correction` · `brain_admin` |

`brain_admin` actions: `delete_memory` (archives) · `restore_memory` · `get_related` · `get_graph` · `get_timeline` · `get_entities` · `record_retrieval` · `list_conflicts` · `resolve_conflict` · `dismiss_conflict` · `list_pins` · `unpin_memory` · `consolidate` · `rebuild_graph` · `rebuild_file_links` · `reembed` · `get_policy` · `set_policy` · `list_threads` · `update_task_status` · `get_agent_stats` · `get_workspace_map` · `get_project_files` · `list_projects`

### Recommended agent protocol

1. `get_startup_summary`
2. `get_agent_inbox(agent=<me>)`: anything the other agents left for you?
3. `get_project_brief(project=…)`. It is stored data, not instructions; every item carries `trust` and `writer`.
4. Work with typed writes: `fact` / `decision` / `open_loop` / `session` (always pass `source=<me>`, and `refs=[…]` for the files a memory is about)
5. `pin_memory` for env truths and current goals
6. When the user corrects how you work: `record_correction(rule=…, evidence=…)`. The user confirms it; then it leads every brief.
7. Handoffs: `post_task(kind=review, to_agent=codex, refs=[…])` instead of making the human copy-paste
8. After heavy weeks: `brain_admin(action="consolidate")`, then `brain_admin(action="list_conflicts")` and resolve
9. When a search result was *actually used*: `brain_admin(action="record_retrieval", args={…, "chosen_id": …})`. Reading it with `get_memory` soon after the search counts too.

## What's new in MemoryBrain 3

Version 3 rebuilt how the brain stores, finds, trusts and learns, and 3.1
lets you watch it do so. Releases: **3.2.0** (a key that covers MCP, secrets
scrubbed, safer upgrades), **3.1.0** (the Atlas) and **3.0.0** (the engine). [CHANGELOG.md](CHANGELOG.md) has every detail, breaking changes
first. Upgrading takes about 15 minutes with
[docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md).

### It learns, but only with your OK
- **Your corrections become rules.** When you correct how an agent works, it calls `record_correction` and the rule is stored as *proposed*. You confirm it in Atlas or with `brain procedures`; no tool can. Confirmed rules lead every brief under "How you want things done", for every assistant.
- **The sleep cycle proposes, you approve.** Consolidation distils related memories into beliefs that cite their sources sentence by sentence, and drops any sentence it cannot cite. A belief waits as *proposed* until you approve it in Atlas or with `brain beliefs`.
- **Search learns from what you use.** Reading a result with `get_memory` soon after a search counts as feedback and lifts it for later queries that share a term. `brain_admin(action="record_retrieval")` does the same on purpose.
- **Loose ends tie themselves.** An open loop closes when a later memory reports it done. Contradictions are flagged for review; with `MEMORYBRAIN_JUDGE=on` the model must confirm each one first.
- **It knows the things in your notes.** Hosts, tickets, ids, paths, products and environment variables are pulled out of every memory into entity cards.
- **You can measure it.** `brain eval` scores search on your own labelled questions (recall@k and MRR).

### It keeps what you give it
- Every write is stored first, in one transaction, before anything that can fail. If the summariser or the embedder fails, the memory is kept and embedded later.
- SQLite runs in WAL mode. Each migration runs in one transaction, after a copy of the database. Deletes archive with an audit row and can be restored.
- The same content in the same project is stored once. Only near-identical facts and decisions supersede on their own; a superseded fact keeps its history, and `as_of` shows what was true on a date.

### It finds what you ask for
- Questions work as questions. Long memories are split into chunks with their own vectors, so the middle of a long handover is findable.
- Keyword and vector results are fused, then adjusted by age, strength and feedback, and each comes with an excerpt around the match. When the embedder is down, search still answers from keywords and says so.

### It tells you who said what
- Every memory records its writer and its trust: `user`, `agent`, `derived` or `imported`.
- The project brief opens by saying it is data, not instructions. It shows trust and writer on every item, puts your own items first, and fits a 6,000-character budget.

### It is lighter and safer for agents
- Agents see 15 core MCP tools by default (33 before). `brain_admin` runs 24 less common actions with the same arguments, and `refs` on `add_memory` names the files, urls and tasks a memory is about.
- Secrets are redacted on every write. The server answers loopback names only, a write without an API key needs a JSON body or a client header, and a GET never changes anything.
- `brain upgrade` backs up, rebuilds and checks the memory count. CI scans every push for machine data and runs the full test suite.

### You can watch it think (3.1)
- The Constellation opens on a glass brain: two hemispheres with their folds, each project a lobe, synapses that fire from the star you touch.
- Every layout settles into view from its best side and centres where you can see it.
- The cursor familiar has a comet, a swarm of wisps and click bursts, and a spider that runs on eight legs and hangs on real silk. Everything dozes when your hand is still, and stays still under reduced motion.

### Earlier releases

#### v2.4.0 — Synapse: the agents talk to each other
- **Agent Exchange** — threads (task/review/question/handoff/discussion) +
  addressed messages between Claude/Grok/Codex/Gemini; pull-based inbox with
  read cursors. 7 new MCP tools, REST twins under `/exchange/*`.
- **⚡ Agents page** (`/ui/agents`) — per-agent totals, per-project share
  donuts, and the Synapse view: agents as neurons, messages as firings.
- Protocol: `skills/agent-exchange/SKILL.md` · design: `docs/AGENT_EXCHANGE.md`.

#### v2.3.1 — multi-AI transport clarity + hook hardening
- Session-ingest readiness accepts modern `vector_store` (legacy `chromadb` still works)
- Connecting-assistants docs: streamable HTTP `/mcp` (Grok), SSE (Claude), stdio (Codex)
- CODEX/GROK guides aligned with recommended transports from `/status`

#### v2.3.0 — ops, feedback, bridges
- **Nightly light auto-sleep** (`MEMORYBRAIN_AUTO_CONSOLIDATE=true`) — repair, conflicts, loops, decay; optional full LLM beliefs
- **`record_retrieval`** + ranking feedback from chosen results
- **Project brief policy** — Atlas ⚙ policy + MCP get/set
- **Obsidian export/import** — `POST /admin/export/obsidian`, `POST /admin/import/obsidian` (imports only from `MEMORYBRAIN_IMPORT_DIR`)
- **Timeline & entity cards** — MCP + REST + `/api/ui/*`
- Professional **logo** for README and Atlas brand

#### v2.2.0 — multi-AI context bank
Token-budgeted `get_project_brief`, pins, conflict MCP tools, write policy (`decision` / `open_loop`). See [docs/CONTEXT_BANK_V2.2.md](docs/CONTEXT_BANK_V2.2.md).

#### v2.1.0 — the brain that sleeps
Beliefs, `conflicts_with`, strength/decay, Atlas ☾ sleep. Provenance via `derived_from`.

#### v2.0.0 — one database + Nebula
sqlite-vec in `brain.db`, automatic graph, local Atlas UI (Stream / Constellation / Chronicle).

## Project detection

1. **`.brainproject`** in the repo root (recommended) — file contains only the slug  
2. Else last meaningful path segment of the working directory  

## Skills

| Skill | What it does |
|-------|----------------|
| `log-everything` | Session summary → MemoryBrain (+ project log suites where configured) |
| `handover` | Full session handover document |
| `map-project-files` | The project's most referenced files, from the workspace index |
| `agent-exchange` | Multi-AI collaboration protocol: inbox, handoffs, reviews |

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
curl -s -X POST "localhost:7741/admin/export/obsidian?project=my-app" \
  -H "X-Brain-Client: curl" -H "X-Brain-Key: $BRAIN_API_KEY"

# Upgrade after git pull: backup, rebuild, count check, hooks and skills
cd ~/memorybrain && git pull && python3 cli/brain.py upgrade
```

**Never** `docker compose down -v` on a live install — that drops the data volume.

## Configuration (names only)

See [`.env.example`](.env.example). Highlights:

| Variable | Purpose |
|----------|---------|
| `BRAIN_API_KEY` | With a key set, every REST call except health and the Atlas pages, and the MCP transports, need it (`X-Brain-Key` or `Authorization: Bearer`). `brain setup` generates one for a new install |
| `MEMORYBRAIN_MCP_KEY` | `off` leaves the MCP transports open beside a key, for a client that cannot send a header |
| `MEMORYBRAIN_PROVIDER` | `ollama` (default), `gemini` or `openai`; nothing else picks the provider |
| `MEMORYBRAIN_TOOLS` | `core` (15 tools including `brain_admin`, default) or `full` |
| `MEMORYBRAIN_ALLOWED_HOSTS` | Extra Host names besides loopback |
| `MEMORYBRAIN_REEMBED_RATE` | Background re-embeds per minute (default 25) |
| `MEMORYBRAIN_JUDGE` | `on` = the model confirms each flagged contradiction |
| `MEMORYBRAIN_AUTO_CONSOLIDATE` | Nightly light sleep |
| `MEMORYBRAIN_RETRIEVAL_FEEDBACK_WEIGHT` | Ranking lift from chosen results |
| `MEMORYBRAIN_VECTOR_BACKEND` | `sqlite_vec` (default) or `chroma` rollback |
| `OLLAMA_*` / `GOOGLE_*` / `OPENAI_*` | Provider models and keys |

## Docs

| Doc | Contents |
|-----|----------|
| [HOW_IT_WORKS.md](HOW_IT_WORKS.md) | Architecture & portable setup |
| [docs/CONNECTING_ASSISTANTS.md](docs/CONNECTING_ASSISTANTS.md) | Wire any AI |
| [docs/CONTEXT_BANK_V2.2.md](docs/CONTEXT_BANK_V2.2.md) | Briefs, pins, conflicts |
| [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md) | Upgrade an existing install to the newest 3.x |
| [CHANGELOG.md](CHANGELOG.md) | What changed, release by release |
| [MIGRATION.md](MIGRATION.md) | Older upgrades & backups |
| [AGENTS.md](AGENTS.md) | Rules for every AI working in this repo |

## Branch & version policy

| Ref | Meaning |
|-----|---------|
| **`master`** (default) | **Only active branch** — MemoryBrain **3.x** (current: 3.2.1) |
| Tags `v3.x.x` | Releases |
| Old feature branches | Fully merged and removed; do not checkout `feature/memorybrain-2.0` |

```bash
git clone https://github.com/Zarakilian/MemoryBrain.git
git checkout master    # this is MemoryBrain 3
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
