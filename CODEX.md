# CODEX.md — MemoryBrain for OpenAI Codex

**Last Updated:** 2026-08-01  
**Companion to:** [`GROK.md`](GROK.md), [`AGENTS.md`](AGENTS.md)

## Why Codex is different

| Client | MCP transport |
|--------|----------------|
| Claude Code | Classic SSE `http://localhost:7741/sse` |
| Grok | Streamable HTTP `http://localhost:7741/mcp` |
| **Codex** | **stdio** via Docker — Codex supports stdio + streamable HTTP, **not** classic SSE |

## Working config (`~/.codex/config.toml`)

```toml
[mcp_servers.memorybrain]
command = "docker"
args = ["exec", "-i", "memorybrain-brain-1", "python", "stdio_server.py"]
startup_timeout_sec = 45.0
tool_timeout_sec = 180.0
enabled = true
```

Requires the brain container running (`memorybrain-brain-1`) with `stdio_server.py` in the image (included since the stdio Dockerfile port).

## After config change

Restart Codex CLI / ChatGPT desktop Codex / IDE extension so MCP reloads.  
Check with `/mcp` or `codex mcp get memorybrain` (should show `transport: stdio`).

## Global Codex helpers

| Path | Role |
|------|------|
| `C:\Users\Miguel\.codex\AGENTS.md` | Session-start MemoryBrain protocol |
| `C:\Users\Miguel\.codex\skills\log-everything\SKILL.md` | End-of-session logging skill |

## Session-start protocol (v2.4)

1. `get_startup_summary`
2. `get_agent_inbox(agent="codex")` — threads Grok/Claude left for you
   (review requests, handoffs, questions). Surface them to the user before
   starting work. Identify as `codex` in every exchange call.
3. `get_project_brief(project=…)` / `get_recent_context`

## Synapse — collaborating with the other agents (v2.4)

Grok typically hands you review work via the exchange:

- Take a review: `get_thread(thread_id)` → `get_memory` on its refs → do the
  review → `reply_to_thread(from_agent="codex", to_agent="grok",
  intent="review", body="…", status="review")`.
- Approve/finish: `intent="approval"` or `intent="done"` + `status="done"`.
- Hand off yourself: save substance with `add_memory(source="codex")` first,
  then `post_task(kind="handoff"/"task", to_agent="grok"/"claude", refs=[…])`.
- Refs over blobs; never paste whole files or secrets into thread bodies.

Full protocol: `skills/agent-exchange/SKILL.md` · design: `docs/AGENT_EXCHANGE.md`.
Analytics UI: `http://localhost:7741/ui/agents`.

## HTTP fallback (when MCP not connected)

If `BRAIN_API_KEY` is set in live `.env`, send `X-Brain-Key` on `/status`, `/ingest/*`, etc. Do not print the key.

## Live vs dev

- Runtime: `C:\Users\Miguel\memorybrain` + Docker volumes  
- This repo: `C:\git\_git\MemoryBrain`  
