# Changelog

All notable changes to MemoryBrain. Versions follow `VERSION`; the running
brain reports its version at `GET /status`.

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
