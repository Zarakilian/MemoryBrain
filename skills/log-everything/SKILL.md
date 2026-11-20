---
name: log-everything
description: Log this session to MemoryBrain so any AI can pick the project up where you stopped. Writes the session log, the durable facts as their own memories, what they replace or finish, the open loops, and the next-session note
version: 3.0.0
disable-model-invocation: false
---

# Log Everything

MemoryBrain is two things, and this skill writes both.

- **The memory** is the log: what was done, fixed and changed this session.
- **The brain** is what the project has learned: the facts, decisions and how-tos that stay true after the session is forgotten.

Done well, the next session (yours or another AI's) starts from where this one stopped, without reading this conversation.

## Step 1. Find the project

Read `.brainproject` in the working directory (or up to four parents). If there is none, use the last meaningful segment of the working directory path (skip `mnt`, `c`, `git`, empty segments).

## Step 2. Read what the brain already holds

Call `get_project_brief(project)`. Note the ids of:

- **open loops** this session finished or moved forward
- **facts or decisions** this session proved wrong, or out of date

You pass those ids in the steps below. The first 8 characters of an id are enough.

## Step 3. Write what was learned, one truth per memory

For each thing that will still be true next month, call `add_memory` once:

| Field | What to pass |
|---|---|
| `type` | `fact` for something that is true. `decision` for a choice the user made, with the reason |
| `content` | One truth, under about 600 characters. Exact names, numbers, ids, dates. How it was proven |
| `summary` | One line a future reader can act on. It is stored word for word |
| `tags` | The topic, the ticket or task id, and `howto` when it is a procedure someone will repeat |
| `refs` | The files it is about (see the path rule below) |
| `supersedes` | `[id]` when this replaces an older memory: the old one is archived |
| `corrects` | `[id]` when only part of an older memory is wrong: it stays, marked disputed |
| `source` | `"log-everything"` |

Skip anything the brief already holds unchanged. Never restate an old fact as a new one.

## Step 4. Write the session log

Call `add_memory` with `type: "session"`, `tags: ["session-log"]`, `source: "log-everything"`.

`content`, 300 to 600 words, under these headings:

- **What we did**: the tasks, in order
- **What changed**: each file changed and what changed in it
- **Fixed**: each bug or blocker, with its cause
- **Decisions**: each choice and who made it
- **State now**: what works, what is half done, what is blocked
- **Not done**: what was planned and did not happen

`summary`: one line, the date and the outcome. Example: `2026-03-02 export job moved to the reporting server, post-check still to run`.

`closes`: the ids of the open loops this session finished. Say so in the text too ("the post-check is done").

`refs`: the files you changed, at most 25.

## Step 5. Write what is still open

One `add_memory` with `type: "open_loop"` per unfinished item. One actionable line: the next action, and who does it. Under 800 characters.

When it updates an older open loop, pass `supersedes: [old id]` so the old one does not stay open beside it.

## Step 6. Record corrections

If the user corrected how you work this session (a preference, a rule, a convention) and you have not recorded it yet, call `record_correction` once per rule:

- `rule`: one short instruction, for example "Always open a pull request, never push to master"
- `evidence`: a short quote of what the user said
- `project`: the project slug, or leave it out when the rule applies everywhere

The rule stays proposed until the user confirms it (Atlas, or `brain procedures`). Never record a rule the user did not state.

## Step 7. Ask for next-session notes

Ask the user: **"Any notes for next session? (tasks, priorities, things to pick up, or press Enter to skip)"**

If they give notes, call `add_memory` with their words as `content`, `type: "note"`, `tags: ["next_session"]`, `source: "log-everything"`.

## Step 8. Confirm

Report:

- the session log id
- how many facts and decisions were stored, and which older memories they replaced or corrected
- which open loops were closed, and which were opened
- next-session notes saved, or skipped

## Writing rules

| Rule | Why |
|---|---|
| Write file paths from the workspace root (`Reports/export/run.py`), or absolute. Never a bare name such as `notes.md` | A bare name fits many files, so the brain cannot link it |
| Add `line` and `commit` to a ref when they matter: `{"path": "src/run.py", "kind": "file", "line": "40-62", "commit": "3f9a2c1d"}` | The next agent opens the right place |
| Write ids exactly: ticket numbers, server names, pull requests as `owner/repo#12` | They are indexed, and `get_entity_context` finds everything about one |
| Name env vars, never their values. No tokens, passwords or connection strings | The brain redacts known secret shapes, but do not rely on it |
| One truth per fact | A fact holding five truths cannot be replaced when one of them changes |
