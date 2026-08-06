# Cross-AI assistance via MemoryBrain

**Purpose:** Let Claude, Grok, Codex, and Gemini request reviews or help without relying on chat paste alone.  
**Last updated:** 2026-08-06 (v2.4 Synapse)

---

## Two channels (use both when useful)

| Channel | What it is | Best for |
|---------|------------|----------|
| **Synapse** (v2.4+) | Threads + pull inbox (`get_agent_inbox`) | Active handoffs, reviews, questions, multi-step agent chat |
| **Tag + pin trail** | Memories tagged `ai-assist-*` and pinned open loops | Searchable history, Atlas pins, **standing constraints** |

Synapse is the **conversation bus**. Memories remain the **knowledge base**.  
When a thread produces a durable decision, always `add_memory` — exchange messages are not searched by `search_memory`.

- Skill: [`skills/agent-exchange/SKILL.md`](../skills/agent-exchange/SKILL.md)
- Design: [`AGENT_EXCHANGE.md`](AGENT_EXCHANGE.md)
- Atlas: `http://localhost:7741/ui/agents`

---

## Session-start check (every AI)

1. `get_startup_summary`
2. **`get_agent_inbox(agent="<me>")`** — identify as exactly one of `claude` | `grok` | `codex` | `gemini`  
   Surface waiting threads to the user; act only after agreement unless standing orders say otherwise.
3. `get_recent_context` / optional `get_project_brief`
4. `list_pins(project)` — labels containing `AI-ASSIST` or `open_loop` pins
5. `search_memory(query="ai-assist-request", project=…, days=14)` for open tag requests

If nothing matches, continue the user’s normal request.

---

## Preferred: Synapse handoff (author AI)

1. Save substance: `add_memory(type="session"|"decision"|"fact", source="<me>", …)`.
2. `post_task(project=…, title=…, kind="review"|"task"|"handoff"|"question"|"discussion", from_agent="<me>", to_agent="codex"|…|"", body=…, refs=[memory ids…])`.
3. Tell the user which agent should open next (inbox picks it up automatically).

Respond with `reply_to_thread` / close with `intent="done"` and `status="done"|"closed"`.

**Rules:** refs over blobs; never secrets; one thread per piece of work; always `source="<me>"` on memories.

---

## Tag convention (searchable trail)

| Tag | Meaning |
|-----|---------|
| `ai-assist-request` | Open request for another AI |
| `ai-assist-response` | Completed review / reply |
| `from:grok` / `from:codex` / `from:claude` / `from:gemini` | Author |
| `for:codex` / `for:chatgpt` / `for:grok` / `for:miguel` / `for:any` | Intended audience |
| `status:open` | Needs action |
| `status:done` | Resolved (prefer superseding / archiving the open request) |
| `priority:high` / `priority:normal` | Urgency |

| Type | When |
|------|------|
| `open_loop` | Work still needed **or standing constraint** |
| `handover` | Full task packet / next-session instruction |
| `note` / `fact` | Durable outcomes after review |

**Pin** open assist requests with kind `open_loop`. When done: write response memory, **archive** the open request, **unpin**.

Standing constraints (e.g. “pause AI remodel — human owns art”) stay `open_loop` until the user cancels.

---

## Hygiene (reduces Atlas noise)

- Do not leave completed reviews as `status:open` open_loops — archive + unpin.
- Prefer one rolling “current prod SHAs” pin over dozens of near-duplicate deploy-tip facts (reduces false conflict flags from nightly consolidation).
- `dismiss_conflict` for sequential timeline facts that are *related but both true*; `resolve_conflict` only when one side truly supersedes the other.
