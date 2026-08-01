# Connecting Any AI Assistant to MemoryBrain

> First install? See [GETTING_STARTED.md](GETTING_STARTED.md).  
> Default branch: **`master`** = MemoryBrain 2.x.

MemoryBrain speaks **MCP** (Model Context Protocol) over two transports plus
a plain **REST API**. Anything that can use one of these three can use the
brain — Claude, Codex, Gemini, Grok, Kimi, Cline, local models, or whatever
ships next. Nothing here is assistant-specific except the config file the
snippet lands in.

## The four doors

| Door | Address | Use when |
|---|---|---|
| MCP streamable HTTP | `http://localhost:7741/mcp` | clients that speak MCP over HTTP (preferred for **Grok**) |
| MCP over SSE | `http://localhost:7741/sse` | classic remote/SSE MCP (**Claude Code**) |
| MCP over stdio | `docker exec -i memorybrain-brain-1 python /app/stdio_server.py` | clients that only launch local commands (**Codex**, Gemini, many IDEs) |
| REST | `http://localhost:7741/...` | no MCP support at all |

Do not point Grok at `/sse` — Grok POSTs initialize to the URL and classic SSE returns 405.

Both MCP doors expose the same **22 tools** (v2.3):

Core: `search_memory`, `get_memory`, `add_memory`, `delete_memory`,
`get_recent_context`, `list_projects`, `get_startup_summary`,
`get_related_memories`, `get_memory_graph`, `consolidate_memory`.

Context bank: `get_project_brief`, `list_conflicts`, `resolve_conflict`,
`dismiss_conflict`, `pin_memory`, `unpin_memory`, `list_pins`.

Ops: `record_retrieval`, `get_timeline`, `get_entities`,
`get_project_policy`, `set_project_policy`.

See [CONTEXT_BANK_V2.2.md](CONTEXT_BANK_V2.2.md) and [GETTING_STARTED.md](GETTING_STARTED.md).

If your container has a different name, find it with `docker ps`
(look for the image built from this repo).

## Claude Code

`python3 cli/brain.py setup --auto-detect` registers it automatically, or:

```bash
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
```

## Gemini (Antigravity)

Registered automatically by `brain setup` when `~/.gemini/antigravity/`
exists; the entry it writes is the generic stdio form below.

## Codex, Kimi, Grok, Cline, and any other MCP client

Most MCP clients accept a JSON `mcpServers` map (some, like Codex CLI, use
the same fields in TOML). Use whichever door your client supports:

```jsonc
// stdio form — works with any client that can launch a command
{
  "mcpServers": {
    "memorybrain": {
      "command": "docker",
      "args": ["exec", "-i", "memorybrain-brain-1",
               "python", "/app/stdio_server.py"]
    }
  }
}
```

```jsonc
// Streamable HTTP — preferred for Grok Build
{
  "mcpServers": {
    "memorybrain": {
      "url": "http://localhost:7741/mcp",
      "type": "http"
    }
  }
}
```

```jsonc
// Classic SSE — Claude Code and similar remote/SSE clients
{
  "mcpServers": {
    "memorybrain": { "url": "http://localhost:7741/sse" }
  }
}
```

TOML (Codex — stdio):

```toml
[mcp_servers.memorybrain]
command = "docker"
args = ["exec", "-i", "memorybrain-brain-1", "python", "stdio_server.py"]
startup_timeout_sec = 45.0
tool_timeout_sec = 180.0
enabled = true
```

TOML (Grok — streamable HTTP):

```toml
[mcp_servers.memorybrain]
url = "http://localhost:7741/mcp"
type = "http"
enabled = true
startup_timeout_sec = 45
tool_timeout_sec = 180
```

Consult your assistant's own MCP documentation for the config file location —
that is the only part that varies.

## REST — for assistants (or scripts) without MCP

```bash
# search (hybrid; falls back to keyword when the embedding provider is down)
curl -s "localhost:7741/api/ui/search?q=deploy+checklist&limit=5"

# recent activity and projects
curl -s localhost:7741/api/ui/stats
curl -s "localhost:7741/api/ui/stream?limit=20"

# store a note (add -H "X-Brain-Key: <key>" if BRAIN_API_KEY is set)
curl -s -X POST localhost:7741/ingest/note \
  -H "Content-Type: application/json" \
  -d '{"content":"the thing to remember","project":"my-project","tags":["ops"]}'
```

Give a non-MCP assistant these endpoints in its system prompt and it can
read and write the brain with plain HTTP calls.

## Notes

- **Auth:** reads under `/api/ui/*` are open on loopback; writes
  (`/ingest/*`, `/api/ui/edit/*`, admin) require the `X-Brain-Key` header
  whenever `BRAIN_API_KEY` is set in `.env`.
- **Installing with an AI's help:** the strict, model-agnostic prompts in
  [AI_INSTALL_PROMPTS.md](AI_INSTALL_PROMPTS.md) drive a full install or
  migration with any assistant.
- MemoryBrain is local-first and loopback-only; none of the above exposes
  it to the network.
