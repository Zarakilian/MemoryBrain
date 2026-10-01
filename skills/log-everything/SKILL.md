---
name: log-everything
description: Log current session to MemoryBrain — saves a session summary as handover memory and prompts for next-session notes
version: 2.0.0
disable-model-invocation: false
---

# Log Everything

Save a comprehensive record of this session to MemoryBrain, then capture any next-session notes for this project.

## Step 1 — Detect project

Check if a `.brainproject` file exists in the current working directory. If it does, read it to get the project slug. If not, use the last meaningful segment of the working directory path as the project slug (exclude: `mnt`, `c`, `git`, empty segments).

## Step 2 — Generate session summary

Analyse the full conversation history and produce a concise session summary (300–600 words) covering:

- **What we worked on** — the main tasks and changes made
- **Key decisions** — important choices and their rationale
- **Files changed** — list the specific files modified and what changed
- **Problems solved** — any bugs fixed or blockers cleared
- **Current state** — what is working, what is in progress, what is blocked

## Step 3 — Save session memory via MCP

Call the `add_memory` MCP tool with:
- `content`: the session summary from Step 2
- `type`: `"session"`
- `project`: the project slug from Step 1
- `tags`: `["session-log"]`
- `source`: `"log-everything"`
- `refs`: the files you changed, as `[{"path": "src/app.py", "kind": "file"}, …]` (at most 25)

The brain redacts known secret shapes, but never put secrets in the summary.

## Step 3b — Record corrections

If the user corrected how you work during this session (a preference, a rule,
a convention) and you have not recorded it yet, call `record_correction` once
per rule:

- `rule`: the rule as one short instruction, e.g. "Always open a pull request, never push to master"
- `evidence`: a short quote of what the user said
- `project`: the project slug, or leave it out when the rule applies everywhere

The rule stays proposed until the user confirms it (Atlas, or `brain procedures`).
Never record a rule the user did not state.

## Step 4 — Ask for next-session notes

Ask the user: **"Any notes for next session? (tasks, priorities, things to pick up — or press Enter to skip)"**

If the user provides notes (non-empty response):
- Call `add_memory` with:
  - `content`: the user's notes
  - `type`: `"note"`
  - `project`: the project slug from Step 1
  - `tags`: `["next_session"]`
  - `source`: `"log-everything"`

## Step 5 — Confirm

Report back:
- Session log saved (show memory ID if available)
- Next-session notes saved (or skipped)
- Reminder: "Next time you start in this project, the session hook will automatically surface your next-session plan."
