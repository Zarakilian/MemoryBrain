# MemoryBrain UI — Clean-Slate Rebuild Brief

> **How to use this document:** paste it (or point the assistant at it) as the
> opening prompt of a fresh session. It contains everything the new session
> needs: ground truth about the backend, the complete failure history of three
> previous UI attempts with root causes, a mandatory diagnosis-first protocol,
> the design vision, and a strict definition of done. The previous session's
> author wrote this as the outgoing lead; the new session owns it from here.

---

## 1. Your role

You are the lead product designer and frontend engineer for MemoryBrain's web
UI. You have **full creative authority** — the owner (Miguel) has explicitly
delegated the vision to you. He is one developer using this tool daily on his
own workstation. He does not want to art-direct you; he wants to open
`http://localhost:7741/ui` and feel that someone with taste built him
something reliable, alive, and beautiful.

Your reputation in this project rests on one thing above all else: **it must
actually work in his browser.** Three previous attempts looked fine in
sandboxed tests and failed in front of him. Read §3 carefully — you inherit
those scars.

## 2. Ground truth — the backend (do not rediscover this, it is verified)

MemoryBrain v2.0.0, repo at `C:\git\_git\MemoryBrain`, branch
`feature/memorybrain-2.0`. FastAPI app in `brain/app/`, served by Docker
Compose on `127.0.0.1:7741` (loopback only). Ollama runs as a second compose
service. Python 3.11-slim image; `Dockerfile` copies `brain/app/` → `/app/app`
so anything under `brain/app/static/` and `brain/app/templates/` ships
automatically. **Every change requires `docker compose build brain && docker
compose up -d` on the owner's machine — he runs this, you cannot.**

Storage: single SQLite file (`/app/data/brain.db` in-container, named volume
`memorybrain_brain_data`): FTS5 keyword index, sqlite-vec embeddings, and a
derived memory graph.

Data model (all verified live):

- `memories(id UUID, content, summary, type[session|handover|note|fact|file|reference], project, tags JSON-array, source, importance 1-5, timestamp ISO, status[active|archived], superseded_by, supersedes, link_degree REAL, linked_at)`
- `projects(slug, name, last_activity, one_liner)`
- `memory_links(src_id, dst_id, kind[semantic|tag|reference|session_chain], weight 0-1, directed 0/1, meta JSON)` + view `memory_links_all`

Read-only JSON endpoints, already stable and tested — **build on these, do not
break them** (the MCP assistant tools share the underlying queries):

- `GET /api/ui/stats` → `{total, by_type:[{type,n}], projects, edges}`
- `GET /api/ui/search?q=&project=&type=&limit=` → `{results:[{id,summary,type,project,importance,timestamp,...}], mode:"hybrid"|"keyword"}` (falls back to keyword when Ollama is down)
- `GET /api/ui/graph?project=&min_weight=&max_nodes=&include_archived=` → `{nodes:[{id,label,type,project,importance,timestamp,degree}], edges:[{src,dst,w,kinds}], truncated}`
- `GET /api/ui/memories/{id}/related?min_weight=&limit=` → `{memory_id, related:[{id,summary,type,project,importance,timestamp,w_combined,kinds,explanations:[{kind,weight,direction:"in"|"out",meta}]}]}`

Auth: optional `BRAIN_API_KEY` header auth exists, but `/ui`, `/api/ui/*` and
`/static/*` **bypass it by design** (see `auth_middleware` in
`brain/app/main.py`). Keep that.

Server-side page routes live in `brain/app/ui/routes.py` (Jinja2 templates in
`brain/app/templates/`, SQL in `brain/app/ui/queries.py`). You may rewrite all
of these freely. Tests: `brain/tests/test_ui.py` (~11 tests) — rewrite them to
match your new UI, keeping equivalent coverage (pages 200, JSON shapes, 404s,
read-only connection, auth bypass, static assets). The other 200+ backend
tests must stay green; you have no reason to touch backend code outside
`brain/app/ui/`, templates, and static.

Vendored already at `brain/app/static/vendor/`: `three.module.min.js` (r170)
+ license. Public-domain da Vinci scans at `brain/app/static/img/`
(vitruvian, flowers, flying_machine, tuscan + CREDITS.md). Use, replace, or
delete any of it — your call.

## 3. The failure history — read this twice

Three UI iterations were built and all three failed in the owner's browser
while passing every sandbox test. The autopsy:

1. **The click-eater (found, but verify it stayed fixed):** `.g-empty`, an
   empty-state overlay with `inset:0`, declared `display:flex` in CSS — which
   silently overrides the HTML `hidden` attribute. An invisible full-canvas
   div swallowed every pointer event. Clicking "highlighted text" because the
   user was selecting its hidden text. The fix (`[hidden]{display:none
   !important}` + `pointer-events:none` on passive overlays) is in the current
   CSS — but the symptom reportedly persisted afterwards, which means EITHER
   the fix never reached his browser (see #2/#3) or a second overlay exists.
2. **Stale assets:** browser caching served old JS/CSS across rebuilds.
   Cache-busting query strings (`?v=4`) were added late. Your build must make
   staleness *impossible to miss* (see §4).
3. **The previous session's tooling had a file-sync fault:** files edited via
   one channel sometimes appeared truncated via another, and at least one
   commit shipped a truncated `requirements.txt` that had to be repaired.
   Trust nothing inherited: **verify byte-integrity of every UI file you keep**
   (`python -m py_compile`, `node --check`, render every template) before
   building on it.
4. **Aesthetic overreach without ground truth:** parchment textures, 3D
   backdrops, marginalia parallax — layered onto an interaction stack nobody
   had ever seen working in the real browser. The owner's verdict: "looks
   awful and still doesn't work." Lesson: **interaction correctness first,
   verified in his browser, before a single decorative pixel.**

## 4. Mandatory protocol — diagnosis before design

Do these before writing any new UI code, in order:

1. **Add a build stamp.** `GET /api/ui/version` returning
   `{"build": "<git short sha or timestamp>"}` baked at container build time,
   and render the same stamp visibly in the UI footer. First question in
   every debugging exchange becomes "what build does the footer say?" — this
   permanently ends the am-I-even-running-new-code class of failure.
2. **Ship a diagnostics page first.** Before the real UI, deliver
   `/ui/doctor`: a dependency-free HTML page (inline CSS/JS, no external
   assets) that runs in-browser checks and prints PASS/FAIL lines: fetch each
   `/api/ui/*` endpoint, create a canvas and a WebGL context, attach a
   pointerdown handler and report "click received at x,y", enumerate any
   element whose bounding box covers >50% of the viewport (overlay detector),
   and dump `window.onerror` captures. Have the owner open it and paste the
   output. **Do not build the pretty UI until /ui/doctor is all-PASS in his
   browser.**
3. **Verify inherited files' integrity** (see §3.3) or simply delete the old
   UI wholesale — cleaner. Recommended: `git rm` the old templates, static
   JS/CSS, and start your structure fresh (keep `queries.py` SQL if useful).
4. **Use the browser, not assumptions.** If Claude-in-Chrome tools are
   available in your session, navigate to the running UI, read the console,
   and take screenshots after every significant change. If not, request
   console output from the owner at each checkpoint (F12 → Console tab). Never
   declare anything fixed without one of the two.

## 5. The vision — "MemoryBrain Atlas" (yours to refine, not to ignore)

The outgoing lead's concept, offered as a strong starting point. You may
evolve it, but any departure should be an upgrade in coherence, not a return
to theme-of-the-week:

**One continuous workspace, not five pages.** A single shell: slim left rail
(projects + counts), a command palette (`Ctrl+K` — search, jump, filter, every
action), a center stage, and a right inspector that slides in when anything is
selected. The center stage offers **three lenses on the same data**, switched
instantly (tabs or `1/2/3` keys), state preserved:

- **Stream** — the default. A reverse-chronological feed of memories grouped
  by day, dense but readable, with type badges, importance ticks, project
  colour accents. This is where daily use lives; it must be excellent.
- **Constellation** — the graph. Force-directed, project-coloured nodes sized
  by link gravity. 2D by default (instant, legible, reliable); a 3D toggle
  can come later *only after 2D interaction is verified in-browser*. Selecting
  a node populates the inspector (summary, metadata, related-with-reasons,
  backlinks) without leaving the view.
- **Chronicle** — a horizontal time axis of sessions/handovers per project
  (the `session_chain` edges are literally this spine), showing how work
  actually flowed. Nobody else's memory tool has this; it's the signature
  view.

**Aesthetic: "instrument, not costume."** Warm dark surface (deep umber-slate,
not pure black), one parchment-cream accent surface for the inspector, brass/
gold used only as the interactive-state colour, a single serif display face
for headings over a quiet sans for data. The da Vinci plates may appear in
exactly one place — a faint engraving inside the empty states — not as page
wallpaper. Motion is feedback only: things move because the user did
something (spring on select, settle on load), never ambient decoration on
functional pages. 60fps or it ships without the effect.

**Engineering rules of the concept:** progressive enhancement — Stream and
inspector are server-rendered and fully usable with JS disabled; palette and
Constellation are enhancements. For the graph, prefer a battle-tested vendored
library over hand-rolled physics (e.g. `force-graph` — vasturiano's 2D canvas
build, single-file dist from the official npm registry, MIT) — the previous
hand-rolled renderers were where the wheels came off. Vendor it locally; no
CDN, fully offline.

## 6. Hard constraints (unchanged from the project charter)

- Local-first, single-user, offline, zero telemetry. No CDN references.
- No build pipeline, no Node server: files served as-is by FastAPI.
  Vendored single-file libs from the official npm registry are fine.
- UI is strictly read-only (`PRAGMA query_only` pattern in `queries.py`).
- Don't break the 9 MCP tools or the `/api/ui/*` shapes (§2).
- Responsive from 1024px laptop to ultrawide; graceful at 720px.
- `prefers-reduced-motion` respected everywhere.
- Every commit: run the full pytest suite, and include
  `Co-authored-by: Claude <noreply@anthropic.com>`.

## 7. Definition of done — all boxes, no exceptions

- [ ] `/ui/doctor` all-PASS **in the owner's actual browser** (pasted output or Chrome-tool screenshot).
- [ ] Footer build stamp matches the freshly built container.
- [ ] Owner confirms, in his browser: click selects a node, drag moves it, scroll zooms, hover shows details — in the Constellation view.
- [ ] Stream, Constellation, Chronicle all render with his real data (hundreds of memories, several projects).
- [ ] Full pytest suite green (rewritten `test_ui.py` included).
- [ ] Every page renders sensibly with an empty database.
- [ ] Assets carry the build stamp in their URLs (no manual cache-bust bumping ever again).
- [ ] `MIGRATION.md`/`README.md` UI sections updated to describe the new interface.
- [ ] Committed on `feature/memorybrain-2.0` with co-author trailer; owner pushes.

Ship the doctor first. Earn the constellation.
