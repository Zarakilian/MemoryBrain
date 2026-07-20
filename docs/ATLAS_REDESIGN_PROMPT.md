# MemoryBrain "One Living Brain" Redesign — copy-paste prompt

Paste the block below into a fresh AI session working in this repo. It
commissions a complete visual/experiential redesign — beyond the da Vinci
parchment era — while protecting every behaviour the owner loves and every
engineering rule this project learned the hard way.

~~~text
You are the new design-and-engineering lead for the MemoryBrain web UI
(FastAPI + Jinja2 + vanilla JS, repo C:\git\_git\MemoryBrain, branch
feature/memorybrain-2.0, served by Docker on 127.0.0.1:7741). You have full
creative authority for a COMPLETE visual redesign. The owner (Miguel) is one
developer using this daily; he wants to open /ui and feel transported.

THE ONE-SENTENCE BRIEF
Make the entire screen feel like a single living organism — one continuous
"MemoryBrain": an immersive, modern, 3D, interactive space where the
memories ARE the world — not a page with sections, and explicitly not the
current layout where the graph looks like a separate app embedded in a
document. No more parchment obligation: the da Vinci era is honourably
retired. Think planetarium, neural nebula, gravity well, black hole
accretion disk of memories, cloth or particle fields that respond to the
cursor — pick ONE strong coherent concept and commit to it completely.

WHAT MUST SURVIVE THE REDESIGN (behaviours, not looks)
- One workspace, three views of the same data: a readable
  reverse-chronological stream, the force-graph constellation (3D default,
  2D fallback), and the chronicle (sessions/handovers per project over
  time, from the session_chain edges). Instant switching, state preserved.
- Ctrl+K command palette (search, jump to project, switch views, actions).
- An inspector for any selected memory: summary, content, tags, metadata,
  related-with-reasons, backlinks — visible and interactive alongside the
  main view, never buried under overlays.
- The glass orb: click a node → camera flies in → the memory's own text
  wraps a transparent, draggable, slowly turning 3D sphere; scroll/Esc
  returns to the exact prior view. Owner loves this — keep or improve it.
- The cursor familiar (footprints → shadow bird → moth → cat, each with
  its own movement personality) with its own on/off toggle. Restyle it to
  the new concept freely; keep its soul.
- Ambient life everywhere: slow wandering background elements, a breathing
  quality, cursor-reactive depth (parallax/tilt), a cursor light-or-shadow
  presence. All remembered toggles (ambience, familiar, theme if any).
- Editing with guardrails: add note/fact/reference + file upload, project
  create/edit, inspector Edit/Archive/Delete (typed-confirm hard delete,
  no deleting non-empty projects), API key prompt on 401.
- Server-rendered, JS-free-usable stream and memory pages (progressive
  enhancement); legacy redirects; empty-DB states that look intentional.

DESIGN RULES LEARNED THE HARD WAY (violating these killed prior versions)
1. UNITY: one material world. If a view needs contrast, blend it into the
   whole (translucent scrims, shared lighting, one palette) — never a
   hard-edged differently-coloured rectangle inside the page. The owner
   rejected exactly that.
2. Content legibility beats mood. Graph nodes must be unmistakable
   (full opacity, luminous against their ground, size/glow meaning
   something) BEFORE any decoration. Text always wins contrast disputes.
3. Background texture must be sub-perceptual: soft tonal variation and/or
   fine generated grain (SVG feTurbulence) only. No dots, specks, crease
   lines, or any countable "event" — they read as dirt.
4. Two motion speeds only: ambient (60s+, drifting, breathing) and
   feedback (~150ms). Nothing in between.
5. THREE.JS INSTANCES DO NOT MIX: never inject objects built with the
   vendored three (r128, window.THREE) into 3d-force-graph's bundled
   three — it fails silently (invisible nodes; this happened). Either use
   each library's own facilities, or own the ENTIRE scene yourself with
   one three instance. For "one living world", owning a single full-screen
   scene (memories, links, background physics — cloth, particles,
   gravity — all in it, DOM panels floating above) is the recommended
   architecture, and vendoring ONE modern three build for it is allowed.
6. Every ambient/decorative layer: pointer-events:none !important, never
   above interactive panels, and beware stacking contexts (a z-index on a
   wrapper once trapped the inspector under a veil).
7. prefers-reduced-motion: the world stands perfectly still, everything
   still works.
8. 60fps or it ships without the effect: transform/opacity animations,
   one pointermove listener + one self-stopping rAF budget for cursor
   effects, instanced/batched geometry for particle counts.

HARD ENGINEERING CONSTRAINTS (unchanged)
- Local-first, fully offline, zero telemetry, no CDN: vendor single-file
  libs from the official npm registry only (force-graph, 3d-force-graph,
  three r128 already vendored; you may add/replace vendored builds).
- No build pipeline: files served as-is by FastAPI from brain/app/static
  and brain/app/templates.
- Do not change /api/ui/{stats,search,graph,memories/{id}/related}
  response shapes (shared with MCP tools). Additive endpoints are fine.
- Reads stay on PRAGMA query_only connections; writes only via
  /api/ui/edit/* (+ /ingest/*), which enforce X-Brain-Key when set.
- Keep /api/ui/version, the footer build stamp, ?b= asset cache-busting,
  and the dependency-free /ui/doctor page working exactly as they are.
- Rewrite brain/tests/test_ui.py expectations as needed but keep
  equivalent coverage; the full pytest suite (235 tests) must be green on
  every commit; every commit carries
  "Co-authored-by: Claude <noreply@anthropic.com>".
- Responsive 1024px → ultrawide; graceful at 720px.

VERIFICATION PROTOCOL (non-negotiable, in this order)
1. Before designing: run the suite; confirm /ui/doctor structure intact.
2. Build incrementally, one coherent commit at a time, suite green each.
3. After the owner rebuilds (docker compose build brain && docker compose
   up -d): footer stamp must match; /ui/doctor all-PASS in HIS browser;
   he confirms click/drag/zoom/hover in the graph and walks every view
   with his real data before you declare anything done.

Definition of done: the owner opens /ui and sees ONE living, breathing,
immersive world — whole-screen, modern, 3D, unmistakably designed — where
reading his stream, flying his constellation, walking his chronicle, and
editing his memories all feel like acts inside the same organism.
~~~
