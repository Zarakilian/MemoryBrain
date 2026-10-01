# MemoryBrain v2.2 — Multi-AI Context Bank

> **Version 3 changes this page.** The brief now opens with an envelope saying
> it is data, not instructions, and every item carries `trust` and `writer`.
> Rules the user confirmed come right after the pins ("How you want things
> done"), facts and decisions are only the current ones, agent-written text is
> capped at 60% of the budget, and `truncated` names the sections that lost
> items. The conflict, pin and policy tools below now run through
> `brain_admin` (for example `brain_admin(action="list_conflicts", args={})`).
> Beliefs from the sleep cycle wait as `proposed` until the user approves them.

**Status:** Implemented on `master`  
**Goal:** Make MemoryBrain the shared operational memory for every assistant
(Grok, Claude, Codex, Gemini) — not a human PKM clone of Obsidian.

## Why this release

v2.1 taught the brain to *sleep* (beliefs, conflicts, strength/decay).  
v2.2 teaches assistants how to *wake up with the right pack* and *resolve
tension* without a human opening Atlas every time.

## Features

### 1. Project brief (`get_project_brief`)
Token-budgeted briefing pack for one project:

| Section | Source |
|---------|--------|
| Pins | `project_pins` (never decay-demoted) |
| Beliefs | type=belief, with `derived_from` citations |
| Facts & decisions | type in fact/decision, high importance first |
| Open loops | type=open_loop or tag `next_session` / loop-like |
| Conflicts | active `conflicts_with` pairs |
| Recent activity | last N days |
| Optional system lane | project `system` truths when policy allows |
| Intent hits | optional hybrid search when `intent` is set |

Hard char budget (default 3500) so every client gets a predictable payload.

### 2. Pins / working set
Per-project pinned memories (`goal`, `truth`, `branch`, `constraint`,
`open_loop`, `custom`). Pins:

- Always appear first in the brief
- Are excluded from consolidation strength decay
- Are MCP-manageable (`pin_memory`, `unpin_memory`, `list_pins`)

### 3. Conflict tools (MCP parity with Atlas)
- `list_conflicts`
- `resolve_conflict` (winner keeps, loser archived)
- `dismiss_conflict` (keep both; tombstone edge)

### 4. Write policy
New types: `decision`, `open_loop`.  
Stricter size limits for fact/decision.  
Sessions remain the place for long narrative dumps.  
Supersession thresholds for decision mirror fact.

### 5. Status matrix
`GET /status` reports version, tool names, transports, and recommended
client configs so adapters can self-heal when the tool surface grows.

### 6. Belief citations on `get_memory`
Belief fetches include `sources` (derived_from memory ids + short previews).

## Edge cases handled

| Case | Behaviour |
|------|-----------|
| Unknown project | Brief returns empty sections + warning, not 500 |
| Pin of missing memory | Reject |
| Pin of archived memory | Reject (must be active) |
| Resolve missing conflict edge | Error |
| Resolve winner==loser | Error |
| Double dismiss | Idempotent-ish (404 if no live conflict) |
| Decay + pins | Pinned ids skipped |
| Belief near its sources | Still cannot auto-supersede non-beliefs |
| Oversized fact/decision | ValidationError with guidance to use session |
| Intent search provider down | Brief still returns structured sections |
| System project missing | `system_ops` empty |
| Char budget exceeded | Truncate lower-priority sections first |

## Non-goals (still not Obsidian)

- Full markdown vault UX
- Bidirectional live sync with Obsidian
- Multi-user ACL

## Recommended agent protocol

1. Session start: `get_startup_summary` then `get_project_brief(project)`  
2. Work: search / get / add with typed writes (`fact`, `decision`, `open_loop`)  
3. Pin durable truths and current goals  
4. After heavy weeks: `consolidate_memory` then `list_conflicts` + resolve  
5. End: session summary + next_session open loop  

## MCP tool count

**17 tools** after v2.2 (was 10 in v2.1).
