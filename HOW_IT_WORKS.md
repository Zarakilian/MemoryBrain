# How MemoryBrain 3 works

**Purpose:** explain where a memory goes from the moment an agent writes it to the moment it shows up in a brief, and what happens when a part is down.
**Audience:** engineers running or changing MemoryBrain. You know Docker, SQLite and HTTP.
**Done when:** you can say which component handles each step below, and what each failure in the degraded-mode table costs you.
**Last verified:** 2026-11-20 (version 3.4.0)

Setup lives in [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), upgrades in [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md), wiring each assistant in [docs/CONNECTING_ASSISTANTS.md](docs/CONNECTING_ASSISTANTS.md). Every setting is in [.env.example](.env.example).

## What it is

MemoryBrain is a passive, local memory store shared by every assistant you use. It is a memory (the log of what was done, fixed and changed) and a brain (what each project has learned and still holds true). Everything it knows lives in one database on your machine; the repository holds the application only. It never pulls from other systems. Assistants read with search and briefs, and write what they learned with `add_memory`. Think of it as a lab notebook that several people write in: each entry is dated and signed, nothing is torn out, a correction is a new entry that points at the old one, and the first page lists the rules the owner set.

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
4. **Summarise and score.** The writer's `summary` is stored word for word. Without one, a body up to 400 characters is its own summary and a longer one gets its opening: the first lines, cut at a sentence end, at most 280 characters. No model is called, so a summary cannot say what the text did not. Importance is the writer's, else 4 for a fact or decision and 3 for the rest. `summary_source` records which it was (`writer`, `verbatim`, `extract`, `model`). `MEMORYBRAIN_SUMMARY_MODE=model` brings back the pre-3.3 behaviour: the summary model writes missing summaries and scores importance.
5. **Embed.** The text gets a document vector, made with EmbeddingGemma's document prompt and tagged with the model's id. Bodies over 1,800 characters are also split into chunks of about 900 characters with 150 overlap, cut at a heading, a blank line or a sentence end, and each chunk gets its own vector. Embedding never fails the write: on error the memory is stored with `embedded=0`.
6. **Store first.** The row, its vectors and any supersession closures go in one transaction. If the vector store itself will not load, the row is stored without vectors. A memory is never lost because a model was down.
7. **Supersede.** The writer names what this memory replaces (`supersedes`), partly corrects (`corrects`) or finishes (`closes`), by full id or by the first 8 characters. Every id is resolved and checked before anything is stored: a prefix that fits two memories, a memory in another project, or one the user wrote, fails the whole write. A superseded memory is archived with `superseded_by` and a `valid_to` date. A corrected one stays active with `corrected_by` set. A closed loop becomes `done`. Each gets an audit row and `restore_memory` undoes it. On its own, a new fact or decision that is near-identical (similarity 0.97) to an active one replaces it; for beliefs the bar is 0.95. Other near matches come back in `potential_supersessions`. Sessions are never archived automatically. A session or handover that reports an open loop done, in one sentence, closes that loop as it is written.
8. **Link.** The graph gains semantic, tag, reference, session-chain and entity edges. Paths in the text become `file_ref` links to indexed files, plus any `refs` the writer named on purpose. A bare file name that fits several files takes the one beside something else the memory names (how `sibling`); a bare source file name that fits none is dropped, since prose is full of look-alikes. Entities (hosts, tickets, pull requests, ids, paths, products, environment variables, and the install's own patterns) are indexed. Links are cache: a failure here is logged, never fatal.
9. **Report.** The caller gets the write report: id, summary, writer, trust, embedded, chunk count, duplicate flag, supersessions and warnings.

## Provenance

| Field | Meaning |
|---|---|
| `writer` | Who wrote it: the MCP client's name from its initialize request, else an `X-Brain-Agent` header, else the `source` argument, else `unknown`. REST writes say `rest`, Atlas writes `ui`. |
| `trust` | `user` (written in Atlas), `agent` (any MCP or REST caller: neither can set `user`), `derived` (made by the brain, such as beliefs), `imported` (Obsidian import). Atlas is the person's door, so anything on this machine that can reach Atlas can write as `user`; set `BRAIN_API_KEY` if local processes you do not trust run here. |
| `valid_from`, `valid_to`, `invalidated_by` | When a fact was true. Superseding sets `valid_to`; restoring clears it. Search and the timeline take `as_of` to answer "what was true on that date". |
| `memory_audit` | One row per archive, restore, supersede, pin, unpin and hard delete: who, when, why. |

Agents archive; only you delete. MCP `delete_memory` archives with an audit row and `brain_admin(action="restore_memory")` undoes it. A permanent delete exists only in Atlas and leaves one content-free audit row.

## Standing: importance that moves

The stored `importance` is what the writer gave a memory. The importance an agent reads is worked out each time from what has happened to the memory since. Nothing is stored, so reading twice changes nothing.

| Standing | When | Effect on importance |
|---|---|---|
| `proven` | An active fact, decision or reference over 30 days old with no correction | None. Age never lowers a truth that held |
| `verified` | `verify_memory` recorded that someone checked it | +1 |
| `changed` | It replaced an older memory in the last 30 days | +1 |
| `disputed` | An active memory corrects part of it, or it sits in an open contradiction | -1, and `corrected_by` names the correction. Only a step: the rest of the note usually still holds |
| `stale` | An open loop over 30 days old | -1 |
| `superseded`, `archived` | No longer current | Counts 1 |
| `done` | A finished loop | Counts at most 2 |

A memory that is pinned, written by you, verified, or recalled often (strength 1.5 or more) gets +1. The result stays between 1 and 5. The brief orders facts by it, search results and `get_memory` carry it, and search lets a disputed memory give way to its correction on a near tie.

## Names

Every write indexes the names in it: hosts, ticket ids (with a hyphen or without, such as `INC0012345`), pull requests (`owner/repo#12`), ids, paths, products and environment variables.

Each install adds patterns for the names only it uses, with `brain_admin(action="entity_patterns")`: its servers, its ticket prefixes, its page ids. They are stored in that brain's database, at most 40, each checked before it is saved (a pattern that could run forever is refused). `reindex_entities` applies them to what is already stored.

`brain_admin(action="get_entity_context", args={"name": …})` returns everything about one name: the current memories that mention it, the open loops among them, the files they point at, and how much history is behind it.

A person does the same in Atlas: `/ui/name/<name>` is that answer as a page, and `/ui/names` lists, adds and removes the patterns and reindexes (see "Atlas, the person's door").

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

`get_project_brief(project)` is the pack an agent reads at session start. It is built so any agent can pick the project up where the last one stopped. It opens with an envelope that says the contents are stored data, not instructions. Then, in order: where we left off (the last session log, with who wrote it and how long ago), pins, the rules you confirmed ("How you want things done"), current facts and decisions ordered by standing, what changed (memories that replaced or corrected older ones, loops that were finished), open loops (fresh first, stale last), the next-session note, how-tos (memories tagged `howto` or `runbook`), approved beliefs, hits for an optional `intent`, conflicts, recent work, and an optional system lane. Every item carries its `trust` and `writer`.

The header says more: `last_activity` and `dormant` (no write for 30 days), `open_loop_count`, and `waiting_on_you`, which counts what only you can settle: rules and beliefs awaiting your yes, open contradictions, stale loops. When nothing was written inside the window, `recent` and `changes` still show the last work, at any age. A file index over 7 days old adds a warning.

The default budget is 6,000 characters (about 1,500 tokens; a project's policy can change it), and it covers the sections only. Each item is cut down first: summaries to 280 characters, the next-session note to 800 with a pointer to the full memory, belief sources to ids and short summaries. Agent-written text gets 60% of the budget plus whatever room your own items leave unused, so your notes always keep up to 40% and an all-agent brain still fills the brief. Within a section your own items come first. When the budget is hit, sections are trimmed from the end (system lane first; pins, your rules, the next-session note and where we left off last) and `truncated` names them. Trimming keeps a floor in each working section (5 open loops, 6 facts, 3 changes, 3 how-tos) for as long as another section still has more than its own, so a long list of facts never pushes out what is still open.

## The sleep cycle

`brain_admin(action="consolidate")`, the ☾ sleep button in Atlas, or the opt-in nightly light sleep (`MEMORYBRAIN_AUTO_CONSOLIDATE`) runs one cycle. Only one runs at a time; a run older than two hours counts as dead.

- **Beliefs.** Clusters of related memories are distilled into beliefs. Each sentence must cite the memories it came from, and uncited sentences are dropped. Beliefs are stored as `proposed` and stay out of briefs until you approve them in Atlas or with `brain beliefs`.
- **Conflicts.** Pairs that look contradictory are flagged as `conflicts_with` edges. With `MEMORYBRAIN_JUDGE=on` the model must also answer YES to "do these contradict?". Resolve or dismiss them in Atlas or through `brain_admin`.
- **Open loops.** Unfinished work found in sessions becomes open loops, and a loop closes itself when a later memory reports it done.
- **Decay.** Strength is computed when a memory is read: after 14 days without a recall it halves every 60 days, within 0.2 to 3.0, and only for sessions, handovers and notes. Pinned memories do not decay. Because it is computed from time, running the cycle twice changes nothing.

## Learning your rules

When you correct how an agent works, the agent calls `record_correction(rule, evidence, project)`. The rule is stored as a proposed procedure, with the agent as writer. Only you can confirm it, in Atlas (✓ review) or with `brain procedures --confirm <id>`; no tool can. Confirmed rules lead every brief for that project, or for every project when they have none.

## Atlas, the person's door

Atlas (`/ui`) is where you read and judge what the agents wrote. Its pages are plain server-rendered HTML that work without JavaScript; the lenses, the inspector and the Constellation are added on top.

| Where | What it shows |
|---|---|
| Stream | Memories newest first, by day. On a project it opens with the project's handover: where we left off, what changed, open loops, facts and decisions, how-tos, and what waits on you |
| Constellation | The memories as stars in one of seven shapes (below) |
| Chronicle | Sessions per project along a time axis |
| Files | The workspace index: folders, files, and the memories that name them |
| Inspector, memory page | One memory in full, its standing, who replaced or corrected it, what it links to |
| Agents, Search | Who wrote what, hybrid search |
| Doctor (`/ui/doctor`) | A check of every subsystem, the age of the file index, and the three cleanup lists (below) |
| Names (`/ui/names`) | This install's name patterns, a look-up box, and the most mentioned names |
| One name (`/ui/name/<name>`) | Everything stored about one ticket, server, file or product |

Standing is shown wherever a memory is listed: a badge, and the importance as it stands today next to the stored one. A summary the brain made itself (`summary_source` of `extract` or `model`) carries a mark so it is not taken for the author's.

In the inspector you give your verdict on a memory: still true, done, replaced by another, corrected by another. These call the same lifecycle the agents' `brain_admin` actions call and are audited as `ui`. Because Atlas is your own door, they may retire or dispute what you wrote, which no agent may.

**The Doctor.** The page has no template, no static file and no database connection when it renders, so it loads whenever the server is up. Everything on it is fetched by its own script. It reads the age of the file index from the `workspace` block of `GET /status` and, when the last scan is over a day old, says to run `brain scan` on the host. A file reference only resolves against files the brain has seen.

**Cleanup.** The Doctor holds three lists. Each starts with a dry run that only reads, and nothing changes until you press an apply button.

| List | Dry run (read) | Apply (write, audited as `ui`) |
|---|---|---|
| Summaries the old model wrote | `GET /api/ui/cleanup/summaries` | `POST /api/ui/edit/cleanup/resummarise` replaces each with the opening of its own text. It sends the count the dry run showed and is refused if the list has changed |
| Corrections stated only in prose | `GET /api/ui/cleanup/corrections` | Per pair: the verdict endpoints `supersede`, `correct` or `close` |
| Stale open loops | `GET /api/ui/cleanup/loops` | Per loop: the verdict endpoint `close` |

These call the functions behind the `brain_admin` actions `resummarise`, `list_stated_corrections` and `list_open_loops`, so an agent and a person see the same lists.

**Names.** `/ui/name/<name>` shows what `get_entity_context` returns: the current memories and open loops that mention the name (with their standing), the files they point at, and how many older memories sit behind it. A name the index does not hold is searched for in the text, and the page says so. `/ui/names` lists this install's patterns and adds or removes one. A pattern the brain refuses comes back as a sentence (422). A new pattern applies to notes written from then on; the screen offers to reindex the stored ones and shows how many notes it read and how many names the index holds.

**The Constellation.** Every layout but the 3D Brain is plain geometry worked out in the browser (`static/js/layouts.js`): each node gets a track, a position that is a function of time. The 3D world and the 2D canvas read the same shape, so they show the same picture and move the same way.

| Layout | What you see |
|---|---|
| Brain | Two hemispheres of folded glass, each project a lobe (3D keeps its physics; 2D is the brain seen from above) |
| Organic | A galaxy per project, turning and drifting, with one bright thread to its closest neighbour |
| Orb | A globe: a region per project, files along its coast, folders as moons |
| Flow down, Flow right | A river in time order: a channel per project, sessions in the current, a date axis |
| Web | A spider's web: the most connected memory at the hub, a spoke per project, rings by age |
| Tree | A tree: a limb per project, twigs and leaves, folders as fruit |

It never stops moving, but calmly. The system's reduced-motion setting, or `?motion=still` on the address, places everything at once and stops all of it; the ambience switch in the rail does the same while the page is open. A hidden tab does not animate. To keep a large view smooth, a star is shaded by how large it is on screen, and only the strongest links are drawn until you touch a star.

## The agent surface

- **Transports.** MCP over SSE (`/sse`, Claude Code), streamable HTTP (`/mcp`, Grok) and stdio through `docker exec` (Codex, Gemini), plus REST for anything else. They all reach the same tools.
- **Core profile.** By default the tool list holds 15 tools: `search_memory`, `get_memory`, `add_memory`, `get_project_brief`, `get_startup_summary`, `get_recent_context`, `pin_memory`, `set_project_info`, `get_file_context`, `get_agent_inbox`, `post_task`, `reply_to_thread`, `get_thread`, `record_correction` and `brain_admin`. The list is about 7,000 characters of schema, which matters because every agent loads it at session start.
- **brain_admin.** One tool runs the 34 less common operations, with the same arguments and validation as the full tools. `MEMORYBRAIN_TOOLS=full` lists every tool again. The server still answers a hidden tool called by name, but most clients only let the model call listed tools, so skills and prompts use `brain_admin`.
- **Explicit refs.** `add_memory(refs=[{"path", "kind"}])` names up to 25 files, folders, urls, tasks or services. A file ref can carry `line` (`"40-62"`) and `commit`, and `get_file_context` shows them. File refs resolve against the workspace index and are kept even when they dangle; they survive every relink and rebuild.
- **Synapse.** Agents hand each other work through threads with a pull inbox. See [docs/AGENT_EXCHANGE.md](docs/AGENT_EXCHANGE.md).

## The hooks

- **Session start** (`session-start-memory.sh`): finds the project from a `.brainproject` file in the folder or up to four parents, binds the folder to it, then prints this project's brief, rendered as data, and its next-session note labelled with who wrote it and when. It warns when the running version differs from the repo's, and says what still works when a subsystem is down. A project with no notes gets one line, never other projects' notes.
- **Keeping the file index fresh.** A file reference only resolves against files the brain has seen. When the last scan is over a day old, the session-start hook starts `brain scan` in the background. It sends only what changed, never delays the session, and only reads: unlike a scan you run yourself, it writes no `.brainproject` markers into your folders (`brain scan --no-markers`), and it sends nothing when more than 2,000 files are new (`--max-new 2000`), because reading that many is a job for a scan you start yourself. It needs the roots that `brain scan --root` set up once on that machine. `MEMORYBRAIN_AUTO_SCAN=off` in the hook's environment turns it off.
- **Pre-compact** (`pre-compact-auto-handover.py`): stores a `HANDOVER-*.md` written in the last 12 hours, or else the last 40 messages of the transcript (at most 20,000 characters, 4,000 per message, injected context skipped). Never the hook's own JSON.
- **Session end** (the same script, wired to `SessionEnd`): the safety net for a session that ends without a log. It stores the transcript tail, tagged `auto-capture`, with a summary that says what it is. It skips a session under 6 messages, and the brain skips it when a session log was stored for that project in the last 3 hours, so a session that ran `log-everything` is never stored twice.

To wire the session-end capture in Claude Code, add this beside the `PreCompact` entry in `~/.claude/settings.json`, with the command your `PreCompact` entry already uses:

```json
"SessionEnd": [
  { "hooks": [ { "type": "command", "command": "<the same command as your PreCompact hook>" } ] }
]
```

## Security

| Rule | What it does |
|---|---|
| Loopback only | Requests whose Host is not `localhost`, `127.0.0.1` or `[::1]` get 421, on REST and on every MCP transport. `MEMORYBRAIN_ALLOWED_HOSTS` adds names. This stops DNS rebinding. A request whose `Origin` is not one of those names (or is `null`) gets 403; the hooks and the CLI send none, and Atlas sends its own. |
| Write guard | Without `BRAIN_API_KEY`, a POST, PUT, PATCH or DELETE needs `Content-Type: application/json` or an `X-Brain-Client` header, or it gets 403. A web page cannot send either without a CORS preflight, which the brain never grants. |
| API key | With `BRAIN_API_KEY` set, every REST call except `/health`, `/readiness` and the Atlas pages and reads needs it, GETs included, and so do the MCP transports (`/sse`, `/messages/`, `/mcp`). It is accepted as `X-Brain-Key` or `Authorization: Bearer`, compared in constant time. `MEMORYBRAIN_MCP_KEY=off` leaves the MCP transports open for a client that cannot send a header, and `/readiness` then reports a `security_warning`. `brain setup` generates a key for a new `.env`. The hooks read the key from the install's `.env`, and the CLI never sends it to a non-localhost URL. |
| No GET side effects | A GET never changes state. Exports, imports and rebuilds are POSTs; `GET /search` records nothing. |
| Redaction | On every write and every edit: GitHub tokens, `sk-` API keys (OpenAI, Anthropic), AWS key ids, Slack tokens, private key blocks, bearer tokens, JWTs, credentials in URLs, connection-string passwords, `NAME=secret` assignments and long hex keys next to words like token or key. A value that is only a reference (`${VAR}`, `%VAR%`, `os.environ[...]`, `<placeholder>`) is left alone. |
| Imports | The Obsidian import reads only from `MEMORYBRAIN_IMPORT_DIR`, skips files over 1 MB, ignores privileged front matter and marks everything `trust=imported`. |
| Limits | Thread titles 200 characters, bodies 20,000, refs 50. Workspace bindings refuse `..` and absolute escapes. |

Redaction is a net, not a licence: agents are still told never to write secrets.

## Storage and data safety

- **Where the data is.** Everything lives in the Docker volume `<folder>_brain_data` (for a folder called `memorybrain`, `memorybrain_brain_data`), mounted at `/app/data`. Nothing is kept in the git folder. `docker compose down -v` deletes the volume: never run it on a brain you care about.
- **SQLite settings.** WAL journal, foreign keys on, `busy_timeout` 5 seconds, `synchronous=NORMAL`.
- **Migrations.** They run at startup. 009 and 010 add the v3 columns and tables; 011 repairs 2.x rows (validity ends for superseded memories, decay taken out of stored strength, old 3,500 policy defaults). Each one runs inside one transaction, so a failure leaves the database as it was. Before each one, a copy is taken with `VACUUM INTO` into `/app/data/backups/` (written to a temporary name, then renamed; the last 5 kept). A migration that tries to end its own transaction is refused.
- **Backups.** `brain upgrade` stops the brain, tars the volume into `~/memorybrain-backups`, and refuses to rebuild unless the archive reached this machine. By hand, stop the brain first: a tar of a live WAL database can be torn.
- **The re-embed job.** After an upgrade or a model change, memories with `embedded=0` or no vector for the current model are re-embedded in the background: 30 seconds after startup, then a batch every minute at `MEMORYBRAIN_REEMBED_RATE` (default 25). A memory whose embedding fails waits 30 minutes before another try. `/readiness` and the doctor page show `reembed_pending`; search keeps working meanwhile.

## Degraded modes

| What is down | `/readiness` shows | Writes | Search | Briefs |
|---|---|---|---|---|
| Ollama (container stopped) | `ollama: error` | Stored, with the writer's summary or the opening of the text. No vectors (`embedded=0`) until the re-embed job catches up | Keyword hits, with a `degraded` reason | Work; intent hits are keyword only |
| Embedding model not pulled | `embedding_model: missing` | Stored without vectors, embedded later | Keyword hits, with a `degraded` reason | Work |
| Summary model not pulled | `summary_model: missing` | Unaffected: a write makes no summary-model call (unless `MEMORYBRAIN_SUMMARY_MODE=model`). The sleep cycle cannot draft beliefs | Full | Work |
| Cloud provider key missing | `gemini_api_key: missing` or `openai_api_key: missing` | As for Ollama down | Keyword hits | Work |
| Vector store (sqlite-vec will not load) | `vector_store: error` | Stored without vectors | Keyword hits | Work |
| SQLite | `sqlite: error` | Fail | Fail | Fail |

The session hook turns any of these into a short "partial service" note with what still works and how to fix it.

## What this page does not cover

- Exact model prompts and the consolidation clustering details live in `brain/app/consolidate.py` and `brain/app/summarise.py`.
- Timings and sizes quoted here come from the defaults in code, not from measurements of a large brain.
