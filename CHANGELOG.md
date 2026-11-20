# Changelog

All notable changes to MemoryBrain. Versions follow `VERSION`; the running
brain reports its version at `GET /status`.

## 3.4.0 (2026-11-20)

Atlas catches up with 3.3, and the Constellation comes alive. Nothing here
changes the agents' tools, the storage or the database: it is the web UI,
the read endpoints behind it, and five write endpoints for the user. Upgrade
with `brain upgrade`. There is no migration.

### The Constellation

- **Every layout looks like its name.** Organic is a field of galaxies, one
  per project, each turning on its own tilt and tied to the others by one
  bright thread. Orb is a globe: a region per project, links as arcs over the
  surface, the far side hidden behind the body. Flow down and Flow right are a
  river: memories in the order they happened, a channel per project, sessions
  in the current, a date axis along the bank. Web is a spider's web: the most
  connected memory at the hub, a spoke per project, the rings by age. Tree is
  a tree: a limb per project, twigs and leaves, folders hanging as fruit.
  Brain keeps its folded glass; its folders and files now sit inside it.
- **Each shape draws what it stands on and names its projects**: the globe's
  graticule and region outlines, the river's water and motes, the web's silk,
  the tree's wood, a glow at the heart of each galaxy.
- **Nothing stands still.** Galaxies turn and drift, the globe spins, the
  river runs, the web trembles, the tree sways, the brain breathes. A bead of
  light travels each link. The sky has three depths of stars that slide
  against each other. Left alone for seven seconds, the camera eases into a
  slow turn (or a gentle sway for a flat shape); any touch stops it. Changing
  layout glides every star to its new place.
- **Still mode.** With the system's reduced-motion setting, or `?motion=still`
  on the address, every star is placed at once and nothing moves. The
  ambience switch in the rail stills the shapes as well as the fog. A hidden
  tab does not animate.
- **2D draws the same shapes.** The flat view is the same geometry projected
  on the page, with the same motion, labels and structures. Its physics is
  switched off, and its colours now match the legend (rank, as in 3D).
- **Standing shows in the stars**: a disputed memory flickers with a red
  edge, one that just replaced another glows, a stale loop burns low, a proven
  or verified one carries a pale ring. The tooltip names the standing and both
  importances.
- **Faster.** About 700 nodes used to cost 40 ms a frame on a laptop graphics
  chip. They now cost 11 to 15 ms (20 ms in the Brain). The star shader works
  by how large a star is on screen, and only the strongest 1,100 links are
  drawn at rest; the others appear when you touch their star.
- `Nebula.advance(seconds)` and `Constellation.debug()` in the browser console
  step the 3D world by hand and report what a frame costs.

### Atlas shows what 3.3 knows

- **Standing on every memory**, wherever one is listed or opened: a small
  badge (proven, verified, changed, disputed, stale, superseded, done), and
  the importance as it stands today with an arrow when it moved from the
  stored one. A disputed memory links to the memory that corrects it; a
  replaced one to its replacement, and the other way round.
- **A project page opens with its handover**, the same sections an agent's
  brief opens with: where we left off, what changed, open loops (stale ones
  last and marked), facts and decisions, how-tos, and a "waiting on you" line
  (rules to confirm, beliefs to approve, contradictions, stale loops) whose
  chips open the review they count.
- **Your verdict on a memory**, in the inspector: still true (verify), done
  (close an open loop), replaced by, corrected by, and take a correction back.
  The picker for the other memory starts from what is already linked and
  searches the project. These run the same lifecycle the agents use, write as
  `ui` in the audit trail, and act as the user, so they may retire or dispute
  what the user wrote. A finished loop can be reopened.
- **A summary the brain made itself is marked** with a small sign in every
  row, and in words on the memory, so it is not taken for the author's.
- New read endpoint `GET /api/ui/brief?project=`. `/api/ui/graph`,
  `/api/ui/stream`, `/api/ui/search`, `/api/ui/memories/{id}` and its
  `/related` gain `standing` and `importance_now`; `importance` stays the
  stored value. The memory detail gains `lineage`.
- New write endpoints under `/api/ui/edit/memories/{id}/`: `verify`, `close`,
  `supersede`, `correct`, `uncorrect`. They need the API key when one is set,
  like every Atlas write.

### Easier to read

- The two quiet text colours were too quiet (about 3:1 on the glass at
  10 px). They are now about 5:1 and 8:1.
- In every list the type badge is a fixed column, so summaries start at the
  same place and the eye can run down them. Types with no colour (decision,
  open loop, procedure) have one.
- Timestamps read `2026-11-20 07:17`, not the raw value with microseconds.
  Search results show the date, not only the time.
- The Constellation controls wrap as whole groups, with the legend and the
  counts on their own line. The 3D box says what is on screen.
- The rail's foot is one block of like buttons. Section headings in the
  inspector and on the memory page have a rule under them.

### Not in this pass

- The Doctor page does not yet show the age of the file index or the cleanup
  lists, and there is no page for one name or screen for name patterns.

## 3.3.0 (2026-11-20)

The memory and the brain. A review of a brain in daily use found the note
bodies good and everything after the write weak: a small model rewrote them
into summaries that were sometimes wrong, a correction left the wrong note
active beside it, open loops never closed, most file references pointed at
nothing, and real ticket and server names were not indexed. This release
fixes those, and makes the brief a handover any assistant can pick up from.
Upgrade with `brain upgrade`, then read "After upgrading" below.

### Changed defaults

- **No model rewrites a summary any more.** `add_memory(summary=…)` is stored
  word for word (`description` still works as the old name). A long memory
  without one gets the opening of its text, cut at a sentence end. Before,
  the summary model wrote it, and a small local model invents details.
  `MEMORYBRAIN_SUMMARY_MODE=model` restores the old behaviour.
- **Importance is no longer scored by the model.** It is the writer's
  (`add_memory(importance=1..5)`), else 4 for a fact or decision and 3 for
  the rest. A write now makes no summary-model call, so it is faster, and the
  summary model is only needed for the sleep cycle.
- **The `importance` an agent reads moves with the memory's standing** (see
  Added). The stored value is `base_importance` on `get_memory`.

### Added

- **Truth maintenance on the write.** `add_memory` takes `supersedes`,
  `corrects` and `closes`: lists of memory ids, full or the first 8
  characters. A superseded memory is archived and points at its replacement,
  a corrected one stays and is marked disputed, a closed loop becomes done.
  Every id is checked before anything is stored, what the user wrote is only
  retired by the user, and each move is audited and undone by
  `restore_memory`. The same moves for stored memories: `brain_admin`
  `supersede`, `correct_memory`, `close_loop`.
- **A session that reports a loop done closes it** as it is written, by the
  rule the sleep cycle already used. The write report lists what closed.
- **Standing.** Every memory has one, worked out when it is read: `proven`
  (old and uncorrected, importance kept), `verified`, `changed` (replaced an
  older memory in the last 30 days, +1), `disputed` (-1, with
  `corrected_by`), `stale` (an open loop over 30 days, -1). Pinned,
  user-written, verified and often-recalled memories get +1.
  `brain_admin(action="verify_memory")` records a re-check. In search a
  disputed memory gives way to its correction on a near tie.
- **The brief is a handover.** New sections `where_we_left_off` (the last
  session log and its writer), `changes` (what replaced, corrected or
  finished something) and `howtos` (memories tagged `howto` or `runbook`).
  Facts are ordered by standing, stale loops come last, and `waiting_on_you`
  counts the rules and beliefs awaiting your yes. A project nobody touched
  inside the window still shows its last work, and `dormant` says so.
- **File references.** Source, config and asset files are recognised in
  text. A bare file name that fits several files takes the one beside
  another file or folder the memory names. A ref can carry `line` and
  `commit`. `/status` reports how old the file index is, and the session
  hook starts `brain scan --no-markers` in the background when it is over a
  day old. That scan only reads: it writes no `.brainproject` markers into
  your folders, which a scan you run yourself still does. With more than
  2,000 new files waiting (`--max-new`) it sends nothing, so a big backlog
  waits for a scan you start.
- **Names.** Ticket ids without a hyphen (`INC0012345`) and pull requests
  (`owner/repo#12`) are indexed. `brain_admin(action="entity_patterns")`
  adds patterns for the names one install uses, stored in its database.
  `get_entity_context` returns everything about one name.
- **Session-end capture.** The capture hook also handles `SessionEnd`: a
  session that ends without a log leaves its transcript tail, unless it was
  under 6 messages or a session log was stored in the last 3 hours.
- `brain_admin` actions for cleaning an existing brain: `resummarise`,
  `list_stated_corrections`, `list_open_loops`, `reindex_entities`.
- The `log-everything` skill writes the session log, the facts as their own
  memories, what they replace, the loops it closed and the loops still open.

### Fixes

- **The image builds again.** Every `google-genai` 0.1.x release was
  withdrawn from PyPI, so `google-genai~=0.1.0` stopped resolving and every
  fresh build, including `brain upgrade`, failed at the package install. The
  requirement is pinned exactly to the version the Gemini provider was
  written against. Moving that provider to the 1.x client is still to do.
- Field and call names (`UserId`, `IsActive`, `GetRowsAsync`) are no longer
  indexed as products, nor file stems (`PROGRESS_LOG.md`) as environment
  variables, nor `DDR4-3200` or `GPT-6` as tickets.
- The Agents page counts a memory under the agent that wrote it (the writer
  the transport reported), not under "other" because its source named a
  skill.
- A project with a confirmed home folder is no longer guessed a second one:
  naming a file under a shared parent folder used to hand that whole parent
  to the project.
- The startup summary shows each project's last session, not whatever
  memory happened to be written last.

### After upgrading

Nothing is changed for you by the upgrade itself: migration `012` only adds
columns. To bring an existing brain up to the new standard, ask your
assistant to run these, in order. Each lists before it changes.

1. `brain_admin(action="resummarise")` shows the summaries it would replace
   with the opening of the text: only the ones that read like the small
   model wrote them (two or three narrative sentences, or a "Here is a
   summary" opening). A line with a writer's marks (an id up front,
   semicolons, backticks, a word in capitals) is left alone, however long,
   so it misses some model summaries rather than replace a good one. Read
   the list, then run it again with `{"dry_run": false}`. The old summary is
   kept in the audit row.
2. `brain_admin(action="list_stated_corrections")` lists memories that say
   in words that they supersede or correct another one that is still
   active. Settle each pair with `supersede`, `correct_memory` or
   `close_loop`. Pairs marked `partial` want `correct_memory`.
3. `brain_admin(action="list_open_loops", args={"stale_only": true})` lists
   the loops over 30 days old. Close the finished ones.
4. `brain_admin(action="entity_patterns", …)` for your own server and id
   shapes, then `brain_admin(action="reindex_entities")`.
5. `python cli/brain.py scan` once, so file references resolve. The session
   hook keeps the index fresh after that.
6. Confirm the rules waiting in Atlas (`/ui`, review) or with
   `brain procedures`. The session brief now says how many are waiting.
7. Wire the session-end capture if you want it: see "The hooks" in
   [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

## 3.2.1 (2026-10-02)

- Every MCP message over the classic SSE transport raised an error after it
  was answered: `/messages/` (and `/sse`) were FastAPI endpoints around a
  transport that writes its own response, so FastAPI sent a second one
  ("Unexpected ASGI message 'http.response.start' sent, after response
  already completed"). Both are raw ASGI routes now. A client that closed
  its session cleanly could hang waiting on the broken reply. Present since
  2.5.0; found in the live log after the 3.2.0 upgrade.

## 3.2.0 (2026-10-02)

Fixes from a full review of 3.0.0 and 3.1.0: a key that now covers MCP,
secrets scrubbed from what was already stored, safer upgrades and
migrations, and many smaller repairs. Upgrade with `brain upgrade`; read
"From 3.1 to 3.2" in [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md) first.

### Breaking changes

- **With `BRAIN_API_KEY` set, the MCP transports need the key too.** `/sse`,
  `/messages/` and `/mcp` answer 401 without `X-Brain-Key: <key>` or
  `Authorization: Bearer <key>` (both now work on REST as well). Register
  Claude Code again with `--header`, and add the header to every other HTTP
  client ([docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)). Stdio clients
  need nothing. `MEMORYBRAIN_MCP_KEY=off` keeps the old open behaviour, and
  `/readiness` then reports a `security_warning`. A brain without a key is
  unchanged.
- **`brain setup` writes a random `BRAIN_API_KEY` into a new `.env`**, and
  registers Claude Code with the key header. An existing `.env` is never
  changed, and `update` and `upgrade` never add a key.

### Fixes

- Archiving and then restoring a proposed or rejected belief or rule no
  longer makes it active: it goes back to the approval queue. Only Approve
  or Confirm activates one, over MCP and in Atlas alike.
- Every Ollama call has a timeout (`MEMORYBRAIN_MODEL_TIMEOUT`, default 120
  seconds). A model server that accepts and never answers can no longer hold
  a write unstored, block search's keyword fallback, or hang `/readiness`
  (now 5 seconds at most).
- Path extraction is bounded: 100,000 characters of path-dense text take
  under a second instead of most of a minute. An Atlas edit and
  `/workspace/scan` index off the event loop.
- Redaction covers tags, source, writer, refs, thread refs, pin labels,
  project names and one-liners, default tags, audit reasons, folder labels
  and file titles, not only the text.
- The hex-key rule no longer redacts identifiers that are called keys
  (dedup, cache, partition, primary and similar keys): they are ids, not
  credentials.
- Secrets stored before 3.0 are redacted once, on the first start of this
  release, after a `VACUUM INTO` backup in `data/backups/` (taken only when
  something changes). Changed memories are re-embedded. The backup keeps the
  old text, so a rule that redacted too much can be undone from it.
- The hooks read `BRAIN_API_KEY` from the install's `.env`, not only from
  the environment. `brain setup`, `update` and `upgrade` record the install
  folder beside the installed hooks (`memorybrain-home`). A hook the brain
  refuses says so instead of reporting the brain as running, and the
  version warning works with a key set.
- The session hook frames the next-session note as data, caps it at 800
  characters, and no longer says "no stored notes" above a note.
- The CLI writes UTF-8 to a pipe on Windows instead of crashing at its
  first emoji.
- `brain setup` keeps a skill you edited, as `update` already did.
- A graph rebuild links sessions across an archived one instead of dropping
  the chain.
- An Atlas content edit marks the memory for re-embedding until its new
  vector is written, so an interrupted edit never leaves the old vector
  passing as current.
- Search hits and `get_memory` carry `trust` and `writer`.
- Summary, importance and embedding run side by side: a slow or hung model
  costs the slowest call, not the sum of three.
- A request whose `Origin` is a foreign site (or `null`) gets 403 on every
  door. The hooks and the CLI send no Origin; Atlas sends its own.
- The Obsidian import skips links that lead out of the import folder, and
  front matter can no longer make a fact or a decision or set importance.
- An Atlas edit meets the limits a write meets (type size caps, tag caps,
  422) and cannot make a second copy of another memory's text (409). Tag
  edits no longer inflate tag counts.
- `as_of` is read once, in UTC, by search and the timeline alike. `20260930`
  and `2026-09-30` both mean the end of that day. REST `/timeline` takes
  `as_of`, and a bad one is an error there too.
- A resolved contradiction closes the loser's validity window, so `as_of`
  still finds it on the days it was current.
- A rule the user rejected cannot be proposed again. The next-session note is
  no longer also listed as an open loop. With the judge on, a pair it cleared
  is not sent to it again every night. Only one process can hold the sleep
  marker at a time.
- The brief says when its task hits are keyword only.
- The hooks skip a Python that does not run (the Windows Store alias), keep
  MEMORY.md's line endings, cut a long folder slug the same way, refuse a URL
  that only starts like localhost, and report a `/readiness` that gives no
  usable answer.
- Migration backups: a crash loop keeps two copies of its own migration and
  never rotates out older ones; a copy cut off by a hard kill is cleaned up.
- The scanner strips URL credentials up to the last `@` before the host.
- A hard delete leaves no row pointing at the deleted memory, and its audit
  row names the rows it had closed. Archiving an archived memory writes no
  second audit row. The unused `auth.py` is gone.
- The hygiene check warns when it loaded no private patterns.
- `brain upgrade` counts a stopped brain from a copy of its volume, so rows
  still in a WAL left by a crash are counted (immutable=1 missed them and
  weakened the "count fell" check). The backup is opened and must hold a
  `brain.db` before anything is rebuilt, not just be a non-empty file.
- A migration that contains its own BEGIN, COMMIT or ROLLBACK is refused
  before any of it runs (it used to commit its first half first).

## 3.1.0 (2026-10-01)

The brain you can see. The Constellation opens on a glass brain, the camera
frames whatever shape you pick, and the cursor familiar has new forms and
real legs. UI only: no migration, no new setting. Rebuild and reload.

### Atlas

- **The Brain layout**, now the default for a first visit: two hemispheres
  with a narrow fissure, a cerebellum and a brainstem, drawn as glass lit at
  its edge and along winding folds, with sparks on the folds and slow waves
  of light. Projects become lobes. A project too big for one lobe spans both
  hemispheres, split by family so related memories stay together. Idle
  synapses stay faint so the folds read; a touched star lights its own.
- **Framing.** When a shape settles, the camera glides to fit the bulk of
  it (a few far planets no longer shrink the view), from that shape's best
  side: the Brain in three-quarter view, flat shapes face on. The stars
  centre in the part of the window you can see, not behind the rail. Your
  hand on the camera always wins. The saved layout applies from the first
  frame, so a load settles once.
- **Living synapses.** Filaments curve and fire: pulses race away from a
  touched star and light ripples out. Far stars twinkle behind the fog.
  Changing layout morphs instead of jumping. A slow machine drops its pixel
  ratio before it drops frames.
- **The familiar** gains a comet and a swarm of wisps, sheds stardust, and
  bursts into starlight where you click. Pick a form in the palette
  (`Familiar: Comet`, `Familiar: Wisps`, ... or `Familiar: Cycle forms`).
  The spider runs on eight legs in a real gait, paced by the ground it
  covers. Its silk is a rope, not a spring: it lowers itself head-down,
  swings like a pendulum when you move, and climbs back hand over hand.
  Every form dozes after 12 seconds of a still hand, so nothing runs while
  you read.
- **Sturdier after a GPU reset** (sleep and wake, a driver hiccup): glows
  and textures come back by themselves, and if the browser will not give 3D
  back, the Constellation shows 2D instead of an empty sky.
- **Glass polish:** starlight runs along the chosen lens, the logo breathes,
  rows warm on hover, scrollbars are thin. All of it stays still under
  reduced motion.
- **For the console:** `Nebula.view("front" | "back" | "left" | "right" |
  "top")`, `Nebula.debug()` (framing and the brain's balance),
  `Nebula.profile(n)` (frame cost with a GPU fence), `Familiar.state()` and
  `Familiar.choose(form)`.

### Build

- The image build rides out a PyPI or network blip (`pip --retries 8
  --timeout 60`). One failed a CI run on 2026-10-01; the same requirements
  built cleanly minutes later.

## 3.0.0 (2026-10-01)

A brain you can trust: nothing an agent writes is lost or silently rewritten,
search finds what you asked for, every memory says who wrote it and how far
to trust it, and the brain learns how you want things done only when you say
so. Upgrade with [docs/UPGRADE_TO_V3.md](docs/UPGRADE_TO_V3.md).

### Breaking changes

- **Core tool profile.** Agents see 15 MCP tools by default instead of 33.
  Everything else runs through `brain_admin(action, args)` with the same
  arguments. Set `MEMORYBRAIN_TOOLS=full` to list every tool again. The
  server still answers a hidden tool called by name, but most clients
  (Claude Code, Codex, Gemini) only let the model call listed tools: a skill
  or prompt that names a hidden tool must use `brain_admin`, or set `full`.
- **Write protection.** Without `BRAIN_API_KEY`, a state-changing request
  needs `Content-Type: application/json` or an `X-Brain-Client` header, or it
  gets 403. A request with a Host header other than localhost, 127.0.0.1 or
  [::1] gets 421 (add names with `MEMORYBRAIN_ALLOWED_HOSTS`). The hooks, the
  CLI, Atlas and the Grok fallback already send what they need.
- **`delete_memory` archives.** Over MCP it archives with an audit row, and
  `brain_admin(action="restore_memory")` undoes it. Only the Atlas UI deletes
  for good.
- **Proposed beliefs.** Beliefs from the sleep cycle are stored as `proposed`
  and stay out of briefs until you approve them in Atlas or with
  `brain beliefs`.
- Smaller ones:
  - `GET /admin/export/obsidian` is now `POST`.
  - The REST inbox no longer marks messages read unless asked (`mark_read=true`).
  - The provider is chosen only by `MEMORYBRAIN_PROVIDER`; an API key alone no longer switches it.
  - An MCP write with no client name, header or `source` records the writer as `unknown` (was `mcp`).
  - `RECENCY_DECAY_RATE` is gone: sessions, handovers and notes age by a fixed curve instead, and facts do not age.
  - `as_of` must be an ISO date or datetime; anything else is an error instead of meaning "now".
  - `GET /exchange/inbox` never marks messages read; `POST /exchange/inbox` does.
  - `add_memory` no longer writes beliefs or rules: beliefs come from the sleep cycle, rules from `record_correction`.
  - `search_memory` returns `{results, degraded}` instead of a list when semantic search is down.
  - A duplicate REST ingest returns 200 with the earlier memory's id.
  - `GET /next-session` with no project returns nothing (2.x returned the newest note of any project).
  - The Obsidian import refuses a folder outside `MEMORYBRAIN_IMPORT_DIR` with 422.

### Data safety

- SQLite runs in WAL mode with foreign keys on, a 5 second busy timeout and
  `synchronous=NORMAL`.
- Each migration runs in one transaction. A copy of the database is taken
  with `VACUUM INTO` before it runs (`backups/` next to `brain.db`, the last 5
  kept), and a script that ends its own transaction is refused.
- Migration 009 only adds: writer, trust, embedded and validity columns,
  chunk vectors, the audit log and the entity tables. Existing rows get safe
  defaults. Migration 010 turns notes tagged `open-loop` into open loops.
  Migration 011 repairs 2.x rows: superseded memories get their validity
  end, 2.x decay is taken out of the strength column (3.0 computes it at read
  time), and policy rows holding the old 3,500 default move to 6,000.
- Store first: a memory is stored in one transaction before anything that can
  fail later. A summariser or embedder failure no longer loses the write; the
  memory is kept, flagged `embedded=0`, and embedded later. The same holds
  when the vector store itself will not load.
- The same content in the same project is stored once; the second write
  returns the first with `duplicate: true`.
- Only near-identical facts and decisions (0.97) and beliefs (0.95) supersede
  automatically. Other near matches come back for review, sessions are never
  auto-archived, and a superseded fact gets a `valid_to` date instead of
  vanishing.
- Archive, restore, supersede and hard delete all write an audit row. A graph
  rebuild keeps conflict verdicts and belief citations.
- Editing a memory in Atlas re-redacts, re-hashes and re-indexes it.

### Search

- Questions work: stopwords are dropped, terms are ORed with prefix matching,
  and FTS syntax in a query can no longer break it.
- Long memories are split into chunks of about 900 characters (150 overlap)
  above 1,800 characters, and each chunk has its own vector, so the middle of
  a long handover is findable.
- Vectors carry their model id and use EmbeddingGemma's document and query
  prompts. Old vectors (`model=''`) stay searchable while a background job
  re-embeds them (`MEMORYBRAIN_REEMBED_RATE`, 25 a minute by default).
- Keyword and vector results are fused with reciprocal rank fusion (k=60),
  then adjusted by age (sessions, handovers and notes only), strength and
  feedback, bounded to 0.7 to 1.3. At most two sessions per project make the
  top of a result list. Results carry an excerpt around the match.
- `as_of` (search, timeline) shows what was valid on a date.
- When the embedder is down, search returns keyword hits with a `degraded`
  reason instead of failing.

### Knowledge

- The sleep cycle computes decay from time when a memory is read, so running
  it twice changes nothing. Only one run happens at a time.
- Beliefs cite their sources sentence by sentence; uncited sentences are
  dropped.
- With `MEMORYBRAIN_JUDGE=on`, the model must confirm each contradiction
  before it is flagged.
- Open loops close themselves when a later memory reports them done.
- Entities (hosts, tickets, ids, paths, products, environment variables) are
  extracted from every memory and shown as cards.

### Learning and provenance

- `record_correction`: when you correct how an agent works, it records the
  rule as a proposed procedure. Only you confirm it (Atlas, or
  `brain procedures`). Confirmed rules lead every brief under "How you want
  things done".
- Every memory records its writer (the MCP client's name, an `X-Brain-Agent`
  header, or the `source`) and its trust (`user`, `agent`, `derived`,
  `imported`).
- The project brief opens with an envelope saying it is data, not
  instructions, shows trust and writer on every item, and names the sections
  it trimmed. Your own items come first; agent-written text gets 60% of the
  budget plus whatever room your items leave unused. Items are cut down
  (summaries 280 characters, the next-session note 800) and point at the
  full memory. The default budget is 6,000 characters and covers the
  sections only; a project's policy can change it, and the REST twin now
  follows that policy too.
- Using a search result (reading it with `get_memory` soon after) counts as
  feedback, but only for later queries that share a term.
- `brain eval` measures recall@k and MRR on a labelled query set.

### Security

- Secrets are redacted on every write (memories, rules, exchange threads,
  project descriptions and policy notes): GitHub, OpenAI and Anthropic, AWS and
  Slack tokens, private keys, bearer tokens, JWTs, credentials in URLs,
  connection-string passwords, `NAME=secret` assignments and long hex keys.
  A value that is only a reference (`${VAR}`, `%VAR%`, `os.environ[...]`) is
  left alone.
- Credentials in git remote URLs are stripped before they are stored or
  shown.
- A GET never changes state: `GET /search` records nothing, and exports and
  imports are POSTs.
- The Obsidian import reads only from `MEMORYBRAIN_IMPORT_DIR`, skips files
  over 1 MB, ignores privileged front matter and marks what it imports as
  `trust=imported`.
- Exchange threads cap titles (200), bodies (20,000) and refs (50).
- Workspace bindings refuse `..` and absolute escapes.
- The CLI never sends the API key to a non-localhost URL.

### Agent surface

- The core profile's tool list serialises to about 7,000 characters, less
  than the old list's 14 most used tools did on their own.
- `brain_admin` runs 24 actions, including `restore_memory`,
  `rebuild_graph`, `rebuild_file_links` and `reembed`.
- `add_memory(refs=[{"path", "kind"}])` names the files, folders, urls,
  tasks and services a memory is about (at most 25). File refs resolve to
  indexed files and are kept even when they dangle.
- Agent docs and skills describe version 3.

### Operations

- `brain upgrade` checks the clone and the volume, counts memories, stops the
  brain, backs the volume up outside the repo, rebuilds, waits for
  readiness, counts again and reinstalls hooks and skills. It refuses when
  anything looks wrong. A hook it replaces keeps a `.bak-<date>` copy beside
  it. A skill you edited (one that matches no version this repo ever
  shipped) is never replaced; the new version is saved beside it as
  `SKILL.md.new`.
- The session hook shows this project's brief only, rendered as data, and
  labels the next-session note with who wrote it and when.
- The pre-compact hook captures the real transcript tail (or a recent
  `HANDOVER-*.md`), never the hook's own JSON.
- Hooks install under the names Claude Code calls, with `.bak` copies.
- `/readiness` reports `reembed_pending` and a provider warning.
- CI runs the hygiene scan and the full test suite on every push.
- Every setting the app reads is documented in `.env.example`, and a test
  keeps it that way.

## 2.5.0 and earlier

See the "What's new" section of the [README](README.md) and
[MIGRATION.md](MIGRATION.md).
