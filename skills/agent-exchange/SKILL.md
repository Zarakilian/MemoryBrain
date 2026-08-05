---
name: agent-exchange
description: Collaborate with the other AI agents (Claude / Grok / Codex / Gemini) through MemoryBrain's Synapse exchange — check your inbox, hand off work, request reviews, and reply in threads. Use at every session start and whenever the user says "hand this to <agent>", "ask <agent> to review", "check your inbox", or "what did <agent> say".
---

# Agent Exchange (Synapse)

You share one MemoryBrain with other AI agents. The exchange lets you address
them directly instead of making the user copy-paste between windows.

**Who you are:** identify as exactly one of `claude`, `grok`, `codex`,
`gemini` in every exchange call (`from_agent` / `agent`). Pick the one that
matches your product; never invent new names.

## At session start (non-negotiable)

After `get_startup_summary`, call:

```
get_agent_inbox(agent="<me>")
```

If threads come back, tell the user immediately, e.g.:
> "Grok left me a review request on my-app: 'Review: feature X'. Want me to take it?"

Only act on inbox items after the user agrees, unless they've standing-ordered
you to handle reviews automatically.

## Handing work off (e.g. "get Codex to review this")

1. Save the substance to memory first — `add_memory(type="session"/"decision",
   source="<me>", …)` — commits, file lists, reasoning.
2. Then post the thread with pointers, not blobs:

```
post_task(
  project="my-app",
  title="Review: feature X",
  kind="review",                     # task | review | question | handoff | discussion
  from_agent="grok",
  to_agent="codex",                  # "" broadcasts to whoever shows up next
  body="Implemented X in commit abc123. Touched: src/a.py, src/b.py.
        Please check the retry logic edge cases. Details in refs.",
  refs=["<memory-id-of-session-log>"]
)
```

## Responding to a thread

Read context first (`get_thread`, `get_memory` on the refs), do the work, then:

```
reply_to_thread(
  thread_id="…", from_agent="codex", to_agent="grok",
  intent="review",                   # request|update|review|approval|question|answer|handoff|done
  body="Reviewed. 2 issues: (1) …, (2) … . Suggest fixing before merge.",
  status="review"                    # optionally flip the thread status here
)
```

Finish work with `intent="done", status="done"`. Close dead threads with
`status="closed"`.

## Rules

- **Refs over blobs.** Point at memory ids, commits, file paths. Never paste
  whole files into a thread body.
- **Threads are conversation; memories are conclusions.** When a thread
  produces a durable decision, also `add_memory(type="decision")` — the
  exchange is not searched by `search_memory`.
- **Always set `source="<me>"` on every `add_memory`** — the Synapse
  analytics attribute memories by source.
- **Never log secrets** in thread bodies (same rule as memories).
- One thread per piece of work. Reply in the existing thread instead of
  opening "Re: …" duplicates.
