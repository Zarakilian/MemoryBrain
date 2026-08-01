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

## HTTP fallback (when MCP not connected)

If `BRAIN_API_KEY` is set in live `.env`, send `X-Brain-Key` on `/status`, `/ingest/*`, etc. Do not print the key.

## Live vs dev

- Runtime: `C:\Users\Miguel\memorybrain` + Docker volumes  
- This repo: `C:\git\_git\MemoryBrain`  
