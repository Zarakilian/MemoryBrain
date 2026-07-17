---
name: log-everything
description: >
  End-of-session logging protocol. Saves a durable session summary, next-session
  notes, and (when in Baby Bee Blossom) the full multi-file project log suite.
  Use when the user says "log everything", "wrap up", "/log-everything",
  "/wrapup", "session log", or asks to record what was done this session.
---

# Log Everything (Grok)

Capture this session so future agents (and you) can resume without re-deriving context.

**Never log secrets**: tokens, passwords, connection strings, private keys, full credential lines from `CONNECTIONS.md`, or raw payment data.

---

## Step 0 — Detect project

1. If workspace has `.brainproject`, use that slug (trim whitespace).
2. Else if path contains `BabyBeeBlossom` (case-insensitive), use `baby-bee-blossom`.
3. Else use the last meaningful directory segment of cwd (skip: `mnt`, `c`, `git`, `_git`, empty).

Set `PROJECT_SLUG` and `TODAY` = local date `YYYY-MM-DD`.

If `PROJECT_SLUG` is `baby-bee-blossom`, **also** run the BBB multi-file protocol in **Step 4** (project skill may refine paths; do not skip files).

---

## Step 1 — Build session summary (required)

From the full conversation, write a **300–700 word** summary covering:

| Section | Content |
|---------|---------|
| What we worked on | Main tasks / goals |
| Key decisions | Choices + rationale |
| Files changed | Paths + what changed (group by area) |
| Commits / deploys | SHAs, branches, Actions/Coolify/Vercel outcomes if any |
| Problems solved | Bugs, root causes, fixes |
| Current state | Working / in progress / blocked |
| Gotchas | Anything the next agent must not re-learn |

Be specific: SHAs, UUIDs, env **names** (not values), URLs of public pages, test evidence.

---

## Step 2 — MemoryBrain (always try)

Prefer MCP tools if connected (`add_memory` / ingest equivalents). Grok MCP is configured with `X-Brain-Key` when `BRAIN_API_KEY` is set on the live service.

If MCP handshake failed, use HTTP. **Auth:** if `C:\Users\Miguel\memorybrain\.env` has a non-empty `BRAIN_API_KEY`, send header `X-Brain-Key: <value>` on every request (do not print the key).

```text
POST http://localhost:7741/ingest/session
Content-Type: application/json
X-Brain-Key: <from live .env if set>

{
  "project": "<PROJECT_SLUG>",
  "source": "log-everything",
  "content": "<session summary>"
}
```

Also post a short note (optional tags via note endpoint if available):

```text
POST http://localhost:7741/ingest/note
X-Brain-Key: <from live .env if set>

{
  "project": "<PROJECT_SLUG>",
  "source": "log-everything",
  "tags": ["session-log"],
  "content": "<1–3 sentence executive summary + tip SHAs>"
}
```

If MemoryBrain is offline (`/status` or `/readiness` fails even with the key): record that in the confirm step and continue with files.

Timeouts of 30–120s on ingest can happen (embedding). Retry once; do not block forever.

At session start (separate from this skill): follow global rule  
`~/.grok/rules/memorybrain-session.md` and read  
`~/.grok/memorybrain/startup-context.md` if present (written by the SessionStart hook).

---

## Step 3 — Ask for next-session notes

Ask the user:

> **Any notes for next session?** (tasks, priorities, pickups — or skip)

If they provide non-empty notes:

- MemoryBrain note with tags `["next_session"]` if available
- For BBB: fold into `CLAUDE_HANDOVERS/NEXT_SESSION_PROMPT.md` and `Grok Files/NEXT_SESSION_NOTES_YYYY-MM-DD.md`

If they skip, write next priorities from **your** assessment of unfinished work.

---

## Step 4 — Project file protocol

### A) Generic project (non-BBB)

If there is a project handover or `DEV_LOG` / `PROJECT_LOG` convention, append once. Otherwise MemoryBrain + your reply is enough.

### B) Baby Bee Blossom — full suite (mandatory when in BBB)

Follow project skill at  
`C:\git\_git\BabyBeeBlossom\.grok\skills\log-everything\SKILL.md`  
(or the same steps inline):

| # | File | Action |
|---|------|--------|
| 1 | `Project Management/DEV_LOG.md` | **APPEND** technical entry/entries (never overwrite) |
| 2 | `Project Management/PROJECT_LOG.md` | **APPEND** session summary |
| 3 | `CLAUDE_HANDOVERS/MEMORY.md` | **APPEND** short session block (10–20 lines) |
| 4 | `CLAUDE_HANDOVERS/NEXT_SESSION_PROMPT.md` | **REWRITE** for next agent (current state + priorities) — **not** the old Frontend_Planning path |
| 5 | `Grok Files/SESSION_LOG_YYYY-MM-DD_Grok_<ShortTitle>.md` | **CREATE** full narrative session log |
| 6 | `Grok Files/NEXT_SESSION_NOTES_YYYY-MM-DD.md` | **CREATE/UPDATE** next-session notes |
| 7 | `Product Blueprint.../DYNAMIC_CONTENT_PLAN.md` | **UPDATE** only if dynamic-content work happened; else skip |
| 8 | MemoryBrain | Already Step 2 |

**DEV_LOG format** (one entry per logical unit if multiple topics; or one session wrap-up if already logged mid-session — avoid pure duplicates; add a wrap-up if only partial logs exist):

```markdown
### [YYYY-MM-DD] - Brief Title
**Category**: Bug Fix | Improvement | New Feature | Configuration | Incident
**Status**: Completed | In Progress | Deferred
**Description**: ...
**Files Modified**: ...
**Issues Encountered**: ...
**Architecture Decisions**: ...
**Build Verification**: ...
**Notes**: ...
```

**Quality rules**

- Read file tails before append so you do not clobber concurrent edits.
- Prefer append for logs; rewrite only NEXT_SESSION_PROMPT and dated next-notes.
- Never print secrets.
- Prefer deployable facts over vibes (“Actions green”, “live page contains X”).

---

## Step 5 — Confirm to user

Report a checklist:

- [ ] MemoryBrain session/note (IDs or “offline / timeout”)
- [ ] Each file path updated
- [ ] Next-session notes (user / agent-derived)
- [ ] Production tips (if anything was deployed)
- [ ] Reminder: next session should load MemoryBrain + `NEXT_SESSION_PROMPT.md` / `GROK.md`

---

## Improvements over Claude/Codex originals

1. **Grok Files/** narrative logs (not only MEMORY).
2. Correct BBB handoff path: `CLAUDE_HANDOVERS/NEXT_SESSION_PROMPT.md`.
3. MemoryBrain **HTTP fallback** when MCP fails.
4. Explicit **deploy/SHA/Actions** capture.
5. **Secret-safe** logging rules.
6. Dedup-aware DEV_LOG (wrap-up vs mid-session entries).
7. Works **outside** BBB as a light MemoryBrain session log.
