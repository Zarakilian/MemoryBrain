# MemoryBrain — MCP Registration + Hook Installation + Testing

## Context

MemoryBrain v0.3.2 is fully built, tested (129 passing), and running on Docker at `http://localhost:7741`. Both Ollama models are pulled. An end-to-end ingest+dedup test has been verified.

**What still needs to happen:** Register it as an MCP server in Claude Code, install the session hooks, test it in a real session, and optionally enable plugins.

GitHub: https://github.com/Zarakilian/MemoryBrain
Local path: /mnt/c/git/_git/MemoryBrain/
Primary log: /mnt/c/git/_git/MemoryBrain/PROGRESS_LOG.md ← READ THIS FIRST
Full setup guide: /mnt/c/git/_git/MemoryBrain/HOW_IT_WORKS.md

---

## Step 1 — Verify Docker is running

```bash
curl -sf http://localhost:7741/health
# Expected: {"status":"ok"}

curl -sf http://localhost:7741/status
# Expected: {"project_count":...,"active_plugins":[],"inactive_plugins":[]}
```

If the brain isn't running:
```bash
cd /mnt/c/git/_git/MemoryBrain
docker compose up -d
```

---

## Step 2 — Register MemoryBrain as an MCP server

```bash
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
```

Verify it's registered:
```bash
claude mcp list
# Should show: memorybrain   http://localhost:7741/sse
```

---

## Step 3 — Install session hooks

The hooks auto-ingest session context at startup and before compaction.

**IMPORTANT:** I already have existing hooks configured in `~/.claude/config.json` for the old MEMORY.md system. The MemoryBrain hooks need to either replace or coexist with those. Check the current hook config:

```bash
cat ~/.claude/config.json | python3 -c "import sys,json; c=json.load(sys.stdin); print(json.dumps(c.get('hooks',{}), indent=2))"
```

The MemoryBrain hooks are:
- `hooks/session-ingest.sh` → session-start hook (fetches startup summary)
- `hooks/pre-compact-ingest.py` → pre-compact hook (ingests session context)

**Option A:** If the existing hooks should be replaced, copy the MemoryBrain hooks:
```bash
cp /mnt/c/git/_git/MemoryBrain/hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp /mnt/c/git/_git/MemoryBrain/hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
chmod +x ~/.claude/hooks/session-start-memory.sh
chmod +x ~/.claude/hooks/pre-compact-auto-handover.py
```

**Option B:** If you want both old and new hooks, add the MemoryBrain hooks alongside the existing ones in `config.json`.

Ask me which approach I want before proceeding.

---

## Step 4 — Test in this session

After registration + hooks, test the MCP tools:

1. **list_projects** — should show at least the "memorybrain" project (from the end-to-end test note stored in the previous session)
2. **search_memory "security hardening"** — should find the test note
3. **add_memory** — store something new:
   ```
   content: "MemoryBrain MCP registration completed successfully"
   type: "note"
   project: "memorybrain"
   ```
4. **get_startup_summary** — should return a compact project list

---

## Step 5 — (Optional) Enable plugins

To enable Confluence auto-ingestion (pulls pages you authored/modified every 6h):
```bash
# Edit .env in /mnt/c/git/_git/MemoryBrain/
CONFLUENCE_URL=https://confluence.derivco.co.za/
CONFLUENCE_TOKEN=<your-PAT-from-Confluence>
```

To enable PagerDuty auto-ingestion (pulls resolved incidents every 2h):
```bash
PAGERDUTY_TOKEN=<your-PD-API-token>
```

After editing `.env`:
```bash
cd /mnt/c/git/_git/MemoryBrain
docker compose restart brain
```

Verify plugins activated:
```bash
curl -sf http://localhost:7741/status
# Should show active_plugins: ["confluence"] and/or ["pagerduty"]
```

---

## Step 6 — (Optional) Enable API key auth

For extra security (recommended if you enable plugins that store sensitive data):

```bash
# Generate a random key
python3 -c "import secrets; print(secrets.token_hex(32))"

# Add to .env
BRAIN_API_KEY=<the-generated-key>

# Restart
docker compose restart brain
```

The hooks automatically pass `BRAIN_API_KEY` as the `X-Brain-Key` header. The MCP SSE endpoint is exempt from auth (MCP has its own transport security).

---

## What to verify at the end

- [ ] `curl http://localhost:7741/health` returns `{"status":"ok"}`
- [ ] `claude mcp list` shows `memorybrain`
- [ ] Claude can call `search_memory`, `add_memory`, `list_projects`
- [ ] New Claude sessions inject the startup summary automatically (if hooks installed)
- [ ] (Optional) `curl http://localhost:7741/status` shows active plugins

---

## Known issues / things to skip

- **ClickHouse plugin:** Leave `CLICKHOUSE_IOM_URL=` blank. It needs a direct HTTP query endpoint, not the MCP proxy URL. The plugin will be inactive — this is fine.
- **Jira plugin:** Still a stub (`jira_stub.py`). Not implemented yet. Silently skipped.
- **`brain` CLI alias:** `brain setup` installs a shell alias. Run it if you want `brain add` / `brain status` commands in your terminal. Not required for MCP functionality.
