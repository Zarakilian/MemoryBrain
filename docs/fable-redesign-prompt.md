# Prompt for Claude Fable — MemoryBrain 2.0 Redesign

> Copy everything below this line into a new conversation with Claude Fable.

---

## Who you are for this task

You are acting as a senior AI-systems architect, backend engineer, and frontend/UX designer combined. I need deep, opinionated, implementation-grade work — not a survey of options. Where there's a clearly better choice, make it and say why. This is a real personal project I intend to build from your output, not a thought experiment.

## What MemoryBrain is (context, not aspiration — this exists and runs today)

MemoryBrain is a local, single-user, Docker-based persistent memory service I built for AI coding assistants (Claude Code, and Gemini/Antigravity, and Codex). It replaces flat `MEMORY.md` files — which have a hard 200-line truncation limit and no search — with a real service that gives an AI assistant automatic context at the start of every session and on-demand recall during the session.

**Current architecture (v0.5.0, working, tested — 109+ passing tests):**

- **FastAPI** app on `localhost:7741`, Docker Compose, non-root container, loopback-only binding.
- **SQLite + FTS5** — canonical store for full content, metadata, BM25 keyword search.
- **ChromaDB** — vector store for embeddings, semantic/cosine search.
- **Hybrid search** — FTS5 keyword results (top 20) + ChromaDB semantic results (top 20) merged via Reciprocal Rank Fusion, with a recency-decay factor so newer memories rank higher. Returns **summaries only**, not full content.
- **AI backend is provider-based** — defaults to local Ollama (`embeddinggemma` for embeddings, `llama3.2:3b` for summarisation/importance scoring), but can be swapped to Gemini or an OpenAI-compatible endpoint via env vars.
- **Data model** — a "memory" has: `id`, `content`, `summary` (2-3 sentence AI-generated), `type` (`session`/`handover`/`note`/`fact`/`file`/etc.), `project` (slug), `tags` (array), `source`, `importance` (1-5, AI-scored), `timestamp`, `chroma_id`. A separate lightweight `projects` table tracks slug, name, last activity, and a one-line auto-summary.
- **Semantic supersession** — new memories that are near-duplicates of old ones auto-archive the old one (type-aware similarity thresholds), full audit trail kept, nothing hard-deleted unless explicitly requested.
- **6 MCP tools** exposed over SSE: `search_memory`, `get_memory`, `add_memory`, `get_recent_context`, `list_projects`, `get_startup_summary` (+ `delete_memory` for hard deletes).
- **Session lifecycle**: a session-start hook calls `/startup-summary` (~150 tokens, injected automatically); a pre-compact hook POSTs the session handover for ingestion (summarised → embedded → scored → stored). No project files are read by the assistant if MemoryBrain reports healthy — it is the authoritative source over stale Markdown.
- **Philosophy (locked in, do not casually discard):** MemoryBrain is a **passive, tool-agnostic store**. It never polls external systems itself. The assistant retrieves data via whatever MCP tools it has (Confluence, PagerDuty, ClickHouse, etc.) and chooses what to save via `add_memory`. This makes it portable — identical behaviour on any machine regardless of which other tools are registered.
- **Data is machine-local by design** — no cross-machine sync, no cloud, no multi-user. This is a personal productivity tool for one developer running it on their own workstation, storing their own work notes and session history. There is no third-party or sensitive personal data involved beyond my own work activity logs.
- Full current docs live at `HOW_IT_WORKS.md`, `README.md`, and `docs/design.md` in the repo — I'm giving you the substance above so you don't need them, but treat this as ground truth for "what exists today."

**Explicitly out of scope in the original design (this is exactly what I now want you to challenge):** the original v0.3 design spec says *"Web UI — Claude is the interface. No need for a browser dashboard."* I've changed my mind — I want a real, beautiful, browsable UI now. Treat that old decision as reversed.

## The problem I want you to solve

I think of Obsidian as the benchmark for "a local-first personal knowledge base with a great graph/linking model and a beautiful UI." I do **not** want to imitate Obsidian's implementation (Electron, plugin ecosystem, human-authored `[[wikilinks]]`, vault-of-flat-files model). I want something **built from first principles for how an AI assistant reads and writes memory**, which then happens to also give a human a browsing experience that's as good as or better than Obsidian's — lighter, faster to load, cheaper to run, and easier for an LLM to reason about than Obsidian's file-and-link model is.

Concretely, Obsidian's strengths I want matched or beaten:
- A visual graph view of how notes/memories relate to each other.
- Backlinks — see everything that references a given note.
- Fast full-text search with instant results.
- A clean, pleasant reading/browsing UI.
- Everything local, private, and durable (plain files you can inspect/back up).

Obsidian's weaknesses I do **not** want to inherit:
- No semantic understanding — links are 100% manually authored `[[..]]` syntax.
- No automatic summarisation, no importance ranking, no dedup.
- Heavy Electron desktop client just to view a graph.
- No concept of "AI-native" access — it's not built to be queried programmatically by an agent as a first-class citizen the way MemoryBrain's MCP tools already are.
- Manual link maintenance doesn't scale — links rot as notes are renamed/moved.

MemoryBrain already beats Obsidian on the AI-facing side (hybrid search, summarisation, importance scoring, MCP-native access, supersession). What it's missing is: (1) a linking/graph layer between memories that's automatically derived (from semantic similarity, shared tags, shared project, explicit references) rather than hand-authored, and (2) any way at all for a human to *see* the data — right now the only interface is the AI assistant itself.

## What I want you to produce

Produce three deliverables, each as its own clearly delimited Markdown document (or code files) so I can save them directly into my repo at `C:\git\_git\MemoryBrain\`. Be exhaustive and specific — file paths, module names, schema changes, actual code where it materially helps, not just prose description.

### Deliverable 1 — `MEMORYBRAIN_2.0_ARCHITECTURE.md`
A redesign/improvement architecture document covering:
- An automatic linking model: how relationships between memories should be derived (similarity threshold graph edges from the existing Chroma embeddings, shared-tag edges, shared-project edges, explicit `source`-based edges, and optionally lightweight LLM-extracted entity/reference links) — with a concrete scoring/edge-weight scheme, not just "use embeddings."
- Whether the current two-store split (SQLite FTS5 + ChromaDB) is still the right choice at this new scope, or whether something lighter (e.g. `sqlite-vec` / `sqlite-vss` to fold vector search into the same SQLite file and drop a whole container) would make the system genuinely lighter and cheaper to run — give me a real recommendation with the trade-offs, not a menu.
- Any schema changes needed to support graph edges, backlinks, and UI-facing queries (e.g. "top memories by centrality," "recent + related") without harming the hot paths the MCP tools already depend on.
- How this stays backward-compatible with the existing 6 MCP tools and the existing data already stored in `memorybrain_brain_data` — I do not want to lose my existing memories or break the Claude/Gemini integration that works today.
- Resource footprint targets — call out explicitly if any change reduces or increases RAM/disk/CPU/container count versus the current Ollama + FastAPI + Chroma stack, since "lighter and cheaper" is a hard goal here, not a nice-to-have.

### Deliverable 2 — `MEMORYBRAIN_2.0_IMPLEMENTATION.md`
A phased implementation/migration guide, written so it could be followed step-by-step (the same way `HOW_IT_WORKS.md` and `docs/design.md` in this repo are written — assume the reader is an AI coding assistant executing the plan autonomously):
- Concrete phases/milestones, each independently shippable and testable, with a rollback path.
- A data migration plan for existing SQLite + Chroma volumes (no data loss).
- New/changed MCP tool surface if any (e.g. a `get_related_memories(id)` or `get_memory_graph(project?)` tool) — full parameter/return shape.
- Testing strategy consistent with the existing 100+ test suite (unit + integration expectations).
- Anything genuinely out of scope, and why.

### Deliverable 3 — A working browser UI design + reference implementation
Design and provide starter/reference code for a local web UI, served locally (extending the existing FastAPI app is fine, e.g. mounting static files + a couple of read-only JSON endpoints), that lets me:
- Browse projects, see recent activity, drill into a memory's full content.
- Search (reusing the existing hybrid search) with instant results.
- **See the graph** — an interactive visual map of memories and their derived links (per-project and global view), with backlinks visible on a memory's detail view.
- Filter/sort by type, project, tag, importance, recency.
- Look good — this should be a genuinely pleasant, modern UI, not a bare data table. But it must stay **light**: no Electron, no heavy SPA build pipeline unless you can justify it's worth the cost versus something like server-rendered HTML + htmx/Alpine.js + a small graph-rendering library (e.g. a lightweight canvas/WebGL graph lib) loaded from a local vendored copy — not a CDN, since this must work fully offline/local.
- Provide the actual file structure and enough real code (HTML/CSS/JS/Python route handlers) that I can drop it into the repo and run it, not just a wireframe description.

## Constraints (do not violate these)

- **Local-first, single-user, no cloud, no telemetry, no data leaving the machine.** This is a personal tool running on my own workstation for my own use — same threat model as Obsidian's local vault.
- Stay in the existing stack family (Python/FastAPI/SQLite/Docker) unless you have a strong, explicitly justified reason to introduce something else — don't introduce a new runtime (e.g. Node.js server, separate frontend build toolchain) just because it's popular.
- Do not break the "passive, tool-agnostic" philosophy — MemoryBrain still must not poll external systems on its own.
- Do not remove or degrade the existing MCP tool contracts without calling it out explicitly as a breaking change with a migration note.
- Favor fewer, lighter dependencies over more, heavier ones. If you introduce a new library, justify it against "could plain code/stdlib/what's already vendored do this almost as well."

## Format of your answer

Reply with the three deliverables as separate, clearly headed Markdown/code blocks in this order: Architecture → Implementation → UI (design + code). Be decisive and specific throughout — file paths, function/endpoint names, schema DDL, and real code snippets wherever they clarify more than prose would.
