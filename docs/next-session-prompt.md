# MemoryBrain — next session prompt (2026-08-01)

## Current state

| Item | Value |
|------|--------|
| Line | MemoryBrain **2.x** on `master` |
| Version / tag | **v2.3.1** @ `4d5e004` |
| Live | `C:\Users\Miguel\memorybrain` (Docker compose project `memorybrain`) |
| Dev | `C:\git\_git\MemoryBrain` |
| Port / UI | `127.0.0.1:7741` · http://localhost:7741/ui |
| Tools | 22 MCP tools |
| Auto-sleep | **ON** — light cycle daily after **03:00 UTC** (`MEMORYBRAIN_AUTO_CONSOLIDATE=true`, `FULL=false`) |

## Client wiring (verified)

| Client | Transport |
|--------|-----------|
| Grok | `http://localhost:7741/mcp` streamable HTTP (`~/.grok/config.toml`) |
| Claude Code | `http://localhost:7741/sse` |
| Codex / ChatGPT | `docker exec -i memorybrain-brain-1 python stdio_server.py` |

## Session start

1. `get_startup_summary`
2. `get_project_brief(project="memorybrain")` (or the workspace slug)
3. Trust Brain over stale `PROGRESS_LOG.md` / this file unless Brain is down

## Do not

- `docker compose down -v` on the live project (destroys `memorybrain_brain_data`)
- Point Grok at `/sse` (405 on initialize)
- Log or print `BRAIN_API_KEY`

## Optional next work

- Review Atlas conflicts (BBB had ~8 after light sleep; not auto-resolved)
- If beliefs from LLM sleep are desired: set `MEMORYBRAIN_AUTO_CONSOLIDATE_FULL=true` and recreate brain container
- Public-repo polish only if Miguel asks

## Related (other project)

Baby Bee Blossom: waiting **Dad second UAT** later today; Codex AI-assist review request **closed** 2026-08-01.
