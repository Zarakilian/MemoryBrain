# Synapse — the Agent Exchange (v2.4)

**Status:** implemented in this branch
**Problem:** Grok, Codex, Claude (and Gemini) all share one MemoryBrain, but they cannot *address each other*. Today the human is the message bus — copy-pasting Grok's diff into Codex for review, pasting Codex's verdict back to Grok. Synapse makes MemoryBrain itself the bus.

## The idea in one paragraph

A **thread** is a unit of collaboration inside a project: a task, a review request, a question, a handoff. Agents post **messages** into threads, addressed to a specific agent (`to_agent="codex"`) or to whoever shows up next (`to_agent=""`). Because agents are session-based and cannot be pushed to, delivery is **pull-based**: every agent calls `get_agent_inbox(agent="codex")` at session start (right after `get_startup_summary`) and sees exactly the threads that are waiting on it, with unread messages. Read cursors make the inbox idempotent — once Codex reads a thread, it stops re-appearing until someone writes to it again.

## Your workflow, concretely

1. You tell **Grok**: "implement feature X, then hand it to Codex for review."
2. Grok codes, saves its session to MemoryBrain as usual, then calls
   `post_task(project="my-app", title="Review: feature X", kind="review", to_agent="codex", body="Implemented X in commit abc123. Files: … Please review edge cases in …", refs=[<memory-ids>])`.
3. Next time you open **Codex** on that project, its session-start protocol calls `get_agent_inbox(agent="codex")` → the review request is there, with Grok's refs resolvable via `get_memory`. Codex reviews, replies with `reply_to_thread(intent="review", body="…3 issues found…", to_agent="grok")` and sets `status="review"`.
4. **Claude**, if present in a later session, sees the open thread via its own inbox (broadcast messages) and can add an opinion with `intent="update"`.
5. Grok fixes, replies `intent="done"`, sets `status="done"`. The thread is the full audit trail.

You stop being the clipboard. You just say *"check your inbox"* — or the agents' standing instructions do it automatically.

## Data model (migration `007_agent_exchange.sql`)

```
agent_threads    id, project, title, kind(task|review|question|handoff|discussion),
                 status(open|in_progress|review|done|closed),
                 created_by, assigned_to, priority, created_at, updated_at

agent_messages   id, thread_id, from_agent, to_agent(''=broadcast),
                 intent(request|update|review|approval|question|answer|handoff|done),
                 body, refs(JSON: memory ids / commits / files), created_at

agent_read_cursors  agent, thread_id, last_read_at   (PK agent+thread_id)
```

Design choices:

- **Dedicated tables, not memories.** Task lifecycle (status transitions, unread tracking) doesn't fit the memory supersession model. Threads are operational, memories are knowledge. A closing agent should still `add_memory(type="decision"/"session")` for anything durable that came out of a thread — the exchange is the conversation, the memory is the conclusion.
- **Pull, not push.** Agents cannot receive webhooks mid-session. The inbox call at session start is the delivery mechanism, and it costs one tool call.
- **Agent names are normalized** (`claude-code` → `claude`, `ChatGPT`/`codex-cli` → `codex`, …) so analytics and addressing stay coherent no matter how each client identifies itself.
- **Refs, not blobs.** Messages carry pointers (memory ids, commit hashes, file paths), not code dumps. The reviewed artifact lives where it already lives.

## MCP tools (7 new → 29 total)

| Tool | Purpose |
|------|---------|
| `post_task` | Create a thread + first message. `kind`, `to_agent`, `refs` optional. |
| `get_agent_inbox` | Threads waiting on this agent (assigned/addressed/broadcast) with unread messages. Marks them read. |
| `reply_to_thread` | Append a message; optionally flip thread status in the same call. |
| `update_task_status` | Move a thread through open → in_progress → review → done/closed. |
| `list_threads` | Browse threads by project/status/agent. |
| `get_thread` | Full transcript of one thread. |
| `get_agent_stats` | Analytics: per-agent memory + message counts, per-project shares, interaction edges. |

REST twins live under `/exchange/*` (API-key protected like other writes) for scripts, plus read-only `/api/ui/agents/*` for the Atlas UI.

## Session protocol addition (all agents)

```
1. get_startup_summary
2. get_agent_inbox(agent="<me>")          ← NEW: anything waiting on me?
3. get_project_brief(project=…)
4. …work…
5. Outbound handoffs: post_task / reply_to_thread with refs to saved memories
6. log-everything as usual
```

The `agent-exchange` skill (in `skills/agent-exchange/`) carries these instructions for Claude/Grok/Codex; `AGENTS.md` makes the inbox call non-negotiable at session start.

## Atlas UI: the Synapse page (`/ui/agents`)

Read-only, like all Atlas surfaces. Three zones:

1. **Totals** — per-agent cards: memories written, messages sent, threads opened, last-seen, ranked by activity. Answers "which AI do I actually use most?"
2. **Per-project share** — donut charts per project of agent contribution (memories + messages), so you can see e.g. Grok owns 70% of project A while Codex dominates project B.
3. **Synapse view** — the fun one: agents as glowing neuron nodes, projects as constellations; interaction edges (who talks to whom) pulse with animated "firings" whose frequency is proportional to recent message volume. Pure canvas, no new vendor deps, honors the existing Nebula aesthetic.

Memory attribution uses normalized `memories.source`; message attribution is exact (`from_agent`). Sources that match no known agent land in "other".

## Non-goals (deliberately)

- No real-time push, presence, or locking — session-based agents can't use them.
- No cross-machine sync (work PC vs home PC each run their own brain; this feature is per-brain).
- No approval workflow enforcement — agents are advisors; you remain the arbiter.

## Compatibility

Purely additive: new tables via migration 007, new tools appended to the registry, new UI page, no changes to existing tool shapes or `/api/ui` contracts. Existing clients are unaffected until they start calling the new tools.
