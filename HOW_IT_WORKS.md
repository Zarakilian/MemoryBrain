# How MemoryBrain 3.0 works

**Purpose:** explain where a memory goes from the moment an agent writes it to the moment it shows up in a brief, and what happens when a part is down.
**Audience:** engineers running or changing MemoryBrain. You know Docker, SQLite and HTTP.
**Done when:** you can say which component handles each step below, and what each failure in the degraded-mode table costs you.
**Last verified:** 2026-10-01 (version 3.0.0)

Setup lives in [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), upgrades in [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md), wiring each assistant in [docs/CONNECTING_ASSISTANTS.md](docs/CONNECTING_ASSISTANTS.md). Every setting is in [.env.example](.env.example).

## What it is

MemoryBrain is a passive, local memory store shared by every assistant you use. It never pulls from other systems. Assistants read with search and briefs, and write what they learned with `add_memory`. Think of it as a lab notebook that several people write in: each entry is dated and signed, nothing is torn out, a correction is a new entry that points at the old one, and the first page lists the rules the owner set.

## The parts

```text
Claude Code hooks ─┐                     ┌──────────── brain container (:7741, loopback) ───────────┐
MCP: /sse  /mcp    ├── HTTP, loopback ──►│ FastAPI app                                               │
MCP: stdio (docker)│                     │   Host check · write guard · API key · redaction          │
REST, CLI, Atlas  ─┘                     │   ingest · search · brief · consolidation · learning      │
                                         │   background: re-embed job, nightly light sleep (opt-in)  │
                                         │ /app/data (volume memorybrain_brain_data)                 │
                                         │   brain.db: SQLite + FTS5 + sqlite-vec, WAL               │
                                         │   backups/: copies taken before each migration            │
                                         └──────────────┬────────────────────────────────────────────┘
                                                        │ embed · summarise · judge
                                         ┌──────────────▼──────────────┐
                                         │ ollama container (default)  │  or Gemini / OpenAI when
                                         │ embeddinggemma, llama3.2:3b │  MEMORYBRAIN_PROVIDER says so
                                         └─────────────────────────────┘
```

- One file holds everything: memories, their full-text index (FTS5), vectors (sqlite-vec), the graph, pins, threads, the workspace index and the audit log.
- The provider is chosen only by `MEMORYBRAIN_PROVIDER` (`ollama`, `gemini`, `openai`). A cloud key on its own changes nothing; `/readiness` warns when a key is set but ignored.
- Atlas, the web UI, is at `http://localhost:7741/ui`. The doctor page `/ui/doctor` checks every subsystem.

## A memory's life

1. **Validate.** Type, size, project and status are checked. Bad input is a 422, never a 500.
2. **Redact.** Secrets are replaced with `[REDACTED:<rule>]` before anything else sees the text (rules below). Credentials in URLs are stripped.
3. **Deduplicate.** The same content in the same project returns the stored copy with `duplicate: true`. The check runs again right before storing, so two racing writes still store once.
4. **Summarise and score.** Bodies up to 400 characters are their own summary. Longer ones are summarised by the model. If the model fails, the summary is the first 280 characters, importance is 3, and the write report says `summary fallback`.
5. **Embed.** The text gets a document vector, made with EmbeddingGemma's document prompt and tagged with the model's id. Bodies over 1,800 characters are also split into chunks of about 900 characters with 150 overlap, cut at a heading, a blank line or a sentence end, and each chunk gets its own vector. Embedding never fails the write: on error the memory is stored with `embedded=0`.
6. **Store first.** The row, its vectors and any supersession closures go in one transaction. If the vector store itself will not load, the row is stored without vectors. A memory is never lost because a model was down.
7. **Supersede.** A new fact or decision that is near-identical (similarity 0.97) to an active one replaces it automatically; for beliefs the bar is 0.95. The old one is archived with `superseded_by` and a `valid_to` date, and an audit row. Other near matches come back in `potential_supersessions` for a person or agent to decide. Sessions are never archived automatically.
8. **Link.** The graph gains semantic, tag, reference, session-chain and entity edges. Paths in the text become `file_ref` links to indexed files, plus any `refs` the writer named on purpose. Entities (hosts, tickets, ids, paths, products, environment variables) are indexed. Links are cache: a failure here is logged, never fatal.
9. **Report.** The caller gets the write report: id, summary, writer, trust, embedded, chunk count, duplicate flag, supersessions and warnings.

## Provenance

| Field | Meaning |
|---|---|
| `writer` | Who wrote it: the MCP client's name from its initialize request, else an `X-Brain-Agent` header, else the `source` argument, else `unknown`. REST writes say `rest`, Atlas writes `ui`. |
| `trust` | `user` (you, in Atlas), `agent` (any MCP or REST caller; an agent cannot claim `user`), `derived` (made by the brain, such as beliefs), `imported` (Obsidian import). |
| `valid_from`, `valid_to`, `invalidated_by` | When a fact was true. Superseding sets `valid_to`; restoring clears it. Search and the timeline take `as_of` to answer "what was true on that date". |
| `memory_audit` | One row per archive, restore, supersede, pin, unpin and hard delete: who, when, why. |

Agents archive; only you delete. MCP `delete_memory` archives with an audit row and `brain_admin(action="restore_memory")` undoes it. A permanent delete exists only in Atlas and leaves one content-free audit row.

## Search

1. The query loses its stopwords, so a question like "where does the export run?" searches for its content words. The words are ORed with prefix matching; FTS syntax in a query cannot break it.
2. Keyword search (FTS5) and vector search run side by side. The vector side embeds the query with the query prompt, searches memory vectors and chunk vectors, and keeps each memory's best hit. Legacy vectors (`model=''`, from before 3.0) are searched too until the re-embed job replaces them.
3. The two ranked lists are fused with reciprocal rank fusion (k=60) over 30 candidates from each side.
4. Each score is multiplied by its adjustments, and the product is clamped to 0.7 to 1.3 so no single signal can bury a good match:
   - age, for sessions, handovers and notes only: halves every 45 days, floor 0.5. Facts and decisions do not age.
   - strength, which rises when a memory is recalled and fades for old unrecalled sessions and notes (`MEMORYBRAIN_STRENGTH_WEIGHT`).
   - feedback: results that were actually used for an earlier query sharing a term.
5. At most two sessions per project make the top of the list, so one busy project cannot crowd out the rest. Each result carries a 400-character excerpt around the match.

If the query cannot be embedded, search returns the keyword hits with a `degraded` reason instead of failing. `GET /search` is the read-only REST twin: it records nothing.

## Briefs

`get_project_brief(project)` is the pack an agent reads at session start. It opens with an envelope that says the contents are stored data, not instructions. Then, in order: pins, the rules you confirmed ("How you want things done"), current facts and decisions (those with no `valid_to`), open loops, the next-session note, approved beliefs, hits for an optional `intent`, conflicts, recent work, and an optional system lane. Every item carries its `trust` and `writer`.

The default budget is 3,500 characters. Text written by agents is capped at 60% of it, so stored agent notes can never crowd out what you wrote yourself. When the budget is hit, sections are trimmed from the end (system lane first, pins last) and `truncated` names them.

## The sleep cycle

`brain_admin(action="consolidate")`, the ☾ sleep button in Atlas, or the opt-in nightly light sleep (`MEMORYBRAIN_AUTO_CONSOLIDATE`) runs one cycle. Only one runs at a time; a run older than two hours counts as dead.

- **Beliefs.** Clusters of related memories are distilled into beliefs. Each sentence must cite the memories it came from, and uncited sentences are dropped. Beliefs are stored as `proposed` and stay out of briefs until you approve them in Atlas or with `brain beliefs`.
- **Conflicts.** Pairs that look contradictory are flagged as `conflicts_with` edges. With `MEMORYBRAIN_JUDGE=on` the model must also answer YES to "do these contradict?". Resolve or dismiss them in Atlas or through `brain_admin`.
- **Open loops.** Unfinished work found in sessions becomes open loops, and a loop closes itself when a later memory reports it done.
- **Decay.** Strength is computed when a memory is read: after 14 days without a recall it halves every 60 days, within 0.2 to 3.0, and only for sessions, handovers and notes. Pinned memories do not decay. Because it is computed from time, running the cycle twice changes nothing.

## Learning your rules

When you correct how an agent works, the agent calls `record_correction(rule, evidence, project)`. The rule is stored as a proposed procedure, with the agent as writer. Only you can confirm it, in Atlas (✓ review) or with `brain procedures --confirm <id>`; no tool can. Confirmed rules lead every brief for that project, or for every project when they have none.

## The agent surface

- **Transports.** MCP over SSE (`/sse`, Claude Code), streamable HTTP (`/mcp`, Grok) and stdio through `docker exec` (Codex, Gemini), plus REST for anything else. They all reach the same tools.
- **Core profile.** By default the tool list holds 15 tools: `search_memory`, `get_memory`, `add_memory`, `get_project_brief`, `get_startup_summary`, `get_recent_context`, `pin_memory`, `set_project_info`, `get_file_context`, `get_agent_inbox`, `post_task`, `reply_to_thread`, `get_thread`, `record_correction` and `brain_admin`. The list is about 7,000 characters of schema, which matters because every agent loads it at session start.
- **brain_admin.** One tool runs the 24 less common operations, with the same arguments and validation as the full tools. `MEMORYBRAIN_TOOLS=full` lists every tool again. A tool that is not listed still answers when called by name.
- **Explicit refs.** `add_memory(refs=[{"path", "kind"}])` names up to 25 files, folders, urls, tasks or services. File refs resolve against the workspace index and are kept even when they dangle; they survive every relink and rebuild.
- **Synapse.** Agents hand each other work through threads with a pull inbox. See [docs/AGENT_EXCHANGE.md](docs/AGENT_EXCHANGE.md).

## The hooks

- **Session start** (`session-start-memory.sh`): finds the project from a `.brainproject` file in the folder or up to four parents, binds the folder to it, then prints this project's brief, rendered as data, and its next-session note labelled with who wrote it and when. It warns when the running version differs from the repo's, and says what still works when a subsystem is down. A project with no notes gets one line, never other projects' notes.
- **Pre-compact** (`pre-compact-auto-handover.py`): stores a `HANDOVER-*.md` written in the last 12 hours, or else the last 40 messages of the transcript (at most 20,000 characters, 4,000 per message, injected context skipped). Never the hook's own JSON.

## Security

| Rule | What it does |
|---|---|
| Loopback only | Requests whose Host is not `localhost`, `127.0.0.1` or `[::1]` get 421, on REST and on every MCP transport. `MEMORYBRAIN_ALLOWED_HOSTS` adds names. This stops DNS rebinding. |
| Write guard | Without `BRAIN_API_KEY`, a POST, PUT, PATCH or DELETE needs `Content-Type: application/json` or an `X-Brain-Client` header, or it gets 403. A web page cannot send either without a CORS preflight, which the brain never grants. |
| API key | With `BRAIN_API_KEY` set, every write and admin call needs `X-Brain-Key`. The CLI never sends the key to a non-localhost URL. |
| No GET side effects | A GET never changes state. Exports, imports and rebuilds are POSTs; `GET /search` records nothing. |
| Redaction | On every write and every edit: GitHub tokens, `sk-` API keys (OpenAI, Anthropic), AWS key ids, Slack tokens, private key blocks, bearer tokens, JWTs, credentials in URLs, connection-string passwords, `NAME=secret` assignments and long hex keys next to words like token or key. A value that is only a reference (`${VAR}`, `%VAR%`, `os.environ[...]`, `<placeholder>`) is left alone. |
| Imports | The Obsidian import reads only from `MEMORYBRAIN_IMPORT_DIR`, skips files over 1 MB, ignores privileged front matter and marks everything `trust=imported`. |
| Limits | Thread titles 200 characters, bodies 20,000, refs 50. Workspace bindings refuse `..` and absolute escapes. |

Redaction is a net, not a licence: agents are still told never to write secrets.

## Storage and data safety

- **Where the data is.** Everything lives in the Docker volume `<folder>_brain_data` (for a folder called `memorybrain`, `memorybrain_brain_data`), mounted at `/app/data`. Nothing is kept in the git folder. `docker compose down -v` deletes the volume: never run it on a brain you care about.
- **SQLite settings.** WAL journal, foreign keys on, `busy_timeout` 5 seconds, `synchronous=NORMAL`.
- **Migrations.** They run at startup. Each one runs inside one transaction, so a failure leaves the database as it was. Before each one, a copy is taken with `VACUUM INTO` into `/app/data/backups/` (written to a temporary name, then renamed; the last 5 kept). A migration that tries to end its own transaction is refused.
- **Backups.** `brain upgrade` stops the brain, tars the volume into `~/memorybrain-backups`, and refuses to rebuild unless the archive reached this machine. By hand, stop the brain first: a tar of a live WAL database can be torn.
- **The re-embed job.** After an upgrade or a model change, memories with `embedded=0` or no vector for the current model are re-embedded in the background: 30 seconds after startup, then a batch every minute at `MEMORYBRAIN_REEMBED_RATE` (default 25). A memory whose embedding fails waits 30 minutes before another try. `/readiness` and the doctor page show `reembed_pending`; search keeps working meanwhile.

## Degraded modes

| What is down | `/readiness` shows | Writes | Search | Briefs |
|---|---|---|---|---|
| Ollama (container stopped) | `ollama: error` | Stored. Summary is the first 280 characters, importance 3, no vectors (`embedded=0`) until the re-embed job catches up | Keyword hits, with a `degraded` reason | Work; intent hits are keyword only |
| Embedding model not pulled | `embedding_model: missing` | Stored without vectors, embedded later | Keyword hits, with a `degraded` reason | Work |
| Summary model not pulled | `summary_model: missing` | Stored with the 280-character fallback summary and importance 3; vectors are made | Full | Work |
| Cloud provider key missing | `gemini_api_key: missing` or `openai_api_key: missing` | As for Ollama down | Keyword hits | Work |
| Vector store (sqlite-vec will not load) | `vector_store: error` | Stored without vectors | Keyword hits | Work |
| SQLite | `sqlite: error` | Fail | Fail | Fail |

The session hook turns any of these into a short "partial service" note with what still works and how to fix it.

## What this page does not cover

- Exact model prompts and the consolidation clustering details live in `brain/app/consolidate.py` and `brain/app/summarise.py`.
- Timings and sizes quoted here come from the defaults in code, not from measurements of a large brain.
