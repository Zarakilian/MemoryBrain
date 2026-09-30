---
name: log-everything
description: >
  End-of-session logging protocol. Saves a durable session summary and
  next-session notes to MemoryBrain, then runs the project's own log protocol
  when the repo has one. Use when the user says "log everything", "wrap up",
  "/log-everything", "/wrapup", "session log", or asks to record what was done
  this session.
---

# Log Everything (Grok)

Capture this session so future agents (and you) can resume without re-deriving context.

**Never log secrets**: tokens, passwords, connection strings, private keys, full credential lines from any connections file, or raw payment data.

---

## Step 0: Detect project

1. If the workspace has `.brainproject`, use that slug (trim whitespace).
2. Else use the last meaningful directory segment of cwd (skip: `mnt`, `c`, `git`, `repos`, `src`, empty).

Set `PROJECT_SLUG` and `TODAY` = local date `YYYY-MM-DD`.

---

## Step 1: Build session summary (required)

From the full conversation, write a **300 to 700 word** summary covering:

| Section | Content |
|---------|---------|
| What we worked on | Main tasks and goals |
| Key decisions | Choices and rationale |
| Files changed | Paths and what changed (group by area) |
| Commits and deploys | SHAs, branches, CI or deploy outcomes if any |
| Problems solved | Bugs, root causes, fixes |
| Current state | Working, in progress, or blocked |
| Gotchas | Anything the next agent must not re-learn |

Be specific: SHAs, env var **names** (not values), URLs of public pages, test evidence.

---

## Step 2: MemoryBrain (always try)

Prefer MCP tools if connected (`add_memory` or the ingest equivalents). Grok MCP is configured with `X-Brain-Key` when `BRAIN_API_KEY` is set on the live service.

If the MCP handshake failed, use HTTP. **Auth:** if the live install's `.env` has a non-empty `BRAIN_API_KEY`, send header `X-Brain-Key: <value>` on every request (do not print the key). Where the live install lives on this machine is in the machine's `.local/` notes.

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

Also post a short note (optional tags via the note endpoint if available):

```text
POST http://localhost:7741/ingest/note
Content-Type: application/json
X-Brain-Key: <from live .env if set>

{
  "project": "<PROJECT_SLUG>",
  "source": "log-everything",
  "tags": ["session-log"],
  "content": "<1 to 3 sentence executive summary + deployed commit SHAs>"
}
```

If MemoryBrain is offline (`/status` or `/readiness` fails even with the key), record that in the confirm step and continue with files.

Timeouts of 30 to 120 s on ingest can happen (embedding). Retry once; do not block forever.

At session start (separate from this skill): follow the session protocol in the repo's `AGENTS.md`.

---

## Step 3: Ask for next-session notes

Ask the user:

> **Any notes for next session?** (tasks, priorities, pickups, or skip)

If they provide non-empty notes, save a MemoryBrain note with tags `["next_session"]`.

If they skip, write next priorities from **your** assessment of unfinished work.

---

## Step 4: Project file protocol

If the repo has its own log skill (for example `<repo>/.grok/skills/log-everything/SKILL.md`), follow it as well, and do not skip any file it lists.

Otherwise, if the repo has a handover or a `DEV_LOG` / `PROJECT_LOG` convention, append once. If it has neither, MemoryBrain plus your reply is enough.

**Quality rules**

- Read file tails before appending so you do not clobber concurrent edits.
- Prefer append for logs; rewrite only next-session prompts and dated next-session notes.
- Never print secrets.
- Prefer deployable facts over impressions ("Actions green", "live page contains X").

---

## Step 5: Confirm to user

Report a checklist:

- [ ] MemoryBrain session and note (ids, or "offline / timeout")
- [ ] Each file path updated
- [ ] Next-session notes (user or agent-derived)
- [ ] Deployed commit SHAs (if anything was deployed)
- [ ] Reminder: next session should load MemoryBrain and the repo's `AGENTS.md`

---

## Improvements over the Claude and Codex originals

1. MemoryBrain **HTTP fallback** when MCP fails.
2. Explicit **deploy / SHA / CI** capture.
3. **Secret-safe** logging rules.
4. Runs the repo's **own** log protocol when one exists.
5. Works in any repo as a light MemoryBrain session log.
