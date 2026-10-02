# Getting started with MemoryBrain

This page is for **someone who just found the repo** and wants a working local brain for their AI assistants. No prior project history required.

**Current line of development:** MemoryBrain **2.x** on the default branch `master` (v2.5.0).  
There is no separate “v2 branch” to checkout — clone `master` and you have MemoryBrain 2.

---

## License

**MIT** — free for individuals and companies (including commercial use).  
See [LICENSE](../LICENSE). Optional paid support / commercial deals:
[COMMERCIAL.md](../COMMERCIAL.md).

## What you get

- A **local** memory service on `127.0.0.1:7741` (not exposed to the internet)
- **MCP tools** so Claude, Grok, Codex, Gemini (and others) share one project-scoped memory
- A **web UI** (Atlas / Nebula) at http://localhost:7741/ui
- Optional **nightly “sleep”**, pins, briefs, conflict tools, Obsidian export

MemoryBrain stores only what assistants (or you) **explicitly save**. It does not scrape your disk or cloud accounts.

---

## Requirements

| Tool | Why |
|------|-----|
| **Docker Desktop** (or Docker Engine + Compose v2) | Runs the brain + Ollama containers |
| **Git** | Clone the repo |
| **Python 3.11+** (optional but recommended) | `cli/brain.py setup` automation |
| ~4–8 GB free disk | Ollama models + SQLite data |
| Internet once | Pull images and models |

**OS:** Linux, macOS, Windows (native or WSL2). On Windows, prefer Docker Desktop with the Linux engine.

---

## Install (recommended path)

```bash
git clone https://github.com/Zarakilian/MemoryBrain.git ~/memorybrain
cd ~/memorybrain
git checkout master          # default; this IS MemoryBrain 2.x
cp .env.example .env

# Automated: Docker up, models, MCP registration, hooks (where supported)
python3 cli/brain.py setup --auto-detect
```

If setup succeeds:

1. Open http://localhost:7741/ui  
2. Check health: `curl -s http://localhost:7741/readiness` → `"ready": true`  
3. Wire your assistant (next section)

### Manual path (if you skip the CLI)

```bash
docker compose up -d --build
# Wait for Ollama, then pull models (names from .env.example):
docker compose exec ollama ollama pull embeddinggemma
docker compose exec ollama ollama pull llama3.2:3b
```

Then register MCP manually using [CONNECTING_ASSISTANTS.md](CONNECTING_ASSISTANTS.md).

### AI-supervised install

Paste a prompt from [AI_INSTALL_PROMPTS.md](AI_INSTALL_PROMPTS.md) into any assistant that can run shell commands. Those prompts are strict and safe for first-time installs.

---

## Connect your assistants

Same tools on every door — pick the transport your client supports:

| Client | How |
|--------|-----|
| **Claude Code** | `claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse` |
| **Grok** | Config: `url = "http://localhost:7741/mcp"`, type HTTP / streamable |
| **Codex / Gemini / most stdio clients** | `docker exec -i memorybrain-brain-1 python stdio_server.py` (or `/app/stdio_server.py`) |

Full snippets: [CONNECTING_ASSISTANTS.md](CONNECTING_ASSISTANTS.md).

**Session habit for agents:**

1. `get_startup_summary`  
2. `get_project_brief(project="your-slug")`  
3. Write with types: `fact` / `decision` / `open_loop` / `session`  
4. Pin durable truths with `pin_memory`  

---

## Tag your projects

Create a `.brainproject` file in each repo root containing only the slug:

```bash
echo "my-app" > /path/to/my-app/.brainproject
```

Without it, MemoryBrain falls back to the last folder name of the working directory.

---

## Optional: API key

`brain setup` writes a random `BRAIN_API_KEY` into a new `.env`. An `.env` that already exists is never changed.

With a key set, every HTTP route except `/health`, `/readiness` and the Atlas pages needs it, reads included, and so do the MCP transports (`/sse`, `/messages/`, `/mcp`). Send it as `X-Brain-Key: <key>` or `Authorization: Bearer <key>`. The stdio transport (`docker exec ... stdio_server.py`) never touches HTTP and needs no key. The hooks read the key from the install's `.env`.

To turn a key on for an existing install:

1. Put `BRAIN_API_KEY=<a long random value>` in `.env`.
2. Register Claude Code again with the header (it keeps it in `~/.claude.json`):
   `claude mcp remove memorybrain -s user`, then
   `claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse --header "X-Brain-Key: <key>"`.
3. Add the same header to Grok's and any other HTTP client's MCP config ([CONNECTING_ASSISTANTS.md](CONNECTING_ASSISTANTS.md)). A client that cannot send a header needs `MEMORYBRAIN_MCP_KEY=off`, which leaves every MCP door open to any local process.
4. Run `python3 cli/brain.py update` once, so the installed hooks know where the `.env` lives.

Restart after changing `.env`:

```bash
docker compose up -d brain
```

---

## Optional: nightly light sleep

In `.env`:

```env
MEMORYBRAIN_AUTO_CONSOLIDATE=true
MEMORYBRAIN_CONSOLIDATE_HOUR=3
```

Then `docker compose up -d brain`. Light mode repairs summaries, flags conflicts, extracts open loops, and decays ranking — it does **not** run full LLM belief distillation unless you also set `MEMORYBRAIN_AUTO_CONSOLIDATE_FULL=true`.

---

## Verify it works

```bash
curl -s http://localhost:7741/health          # {"status":"ok"}
curl -s http://localhost:7741/readiness       # ready: true
curl -s http://localhost:7741/status          # version, tools, scheduler
# (add -H "X-Brain-Key: …" if BRAIN_API_KEY is set)

# Store a test note (key if configured)
curl -s -X POST http://localhost:7741/ingest/note \
  -H "Content-Type: application/json" \
  -d '{"content":"MemoryBrain installed","project":"setup-test","tags":["setup"]}'
```

In Atlas: http://localhost:7741/ui — you should see the note under project `setup-test`.

---

## Day-to-day commands

```bash
cd ~/memorybrain
git pull origin master
docker compose build brain && docker compose up -d

docker compose logs -f brain          # logs
docker compose ps                     # containers
```

**Never** run `docker compose down -v` on a machine you care about — `-v` deletes the named data volume (`brain_data`) and **wipes all memories**.

Back up once in a while, with the brain stopped (a copy of a live WAL
database can be torn) and into a folder outside the repo:

```bash
mkdir -p ~/memorybrain-backups
docker compose stop brain
docker run --rm -v memorybrain_brain_data:/data:ro -v ~/memorybrain-backups:/backup alpine \
  tar czf /backup/brain-backup-$(date +%Y%m%d).tar.gz -C /data .
docker compose start brain
```

PowerShell: the same commands, with `"$HOME\memorybrain-backups:/backup"` as the second
mount. The volume is named after the install folder (`memorybrain_brain_data` for a
folder called `memorybrain`); `docker volume ls` shows yours.

---

## Upgrading

- **From 2.x or an older 3.x:** follow [UPGRADE_TO_V3.md](UPGRADE_TO_V3.md). If your clone was
  made before 2026-09-30, rename it and clone again first. Then
  `python3 cli/brain.py upgrade` backs up, rebuilds and checks the memory count.
- **From 0.5.x:** back up (see above), then follow [MIGRATION.md](../MIGRATION.md),
  or let an assistant drive it with [AI_INSTALL_PROMPTS.md](AI_INSTALL_PROMPTS.md).

You do **not** need the old `feature/memorybrain-2.0` branch — it is fully merged into `master`.

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `ready: false` | `docker compose ps`; Ollama models pulled; `docker compose logs brain` |
| Assistant has no tools | Correct transport (SSE vs HTTP vs stdio); container name `memorybrain-brain-1` |
| 401 on `/status` or ingest | Send `X-Brain-Key` matching `.env` |
| Empty brief | Write some memories for that project slug first |
| Port in use | Change `BRAIN_PORT` in `.env` |

Doctor UI: http://localhost:7741/ui/doctor  

---

## Where to read next

| Doc | Use when |
|------|----------|
| [README.md](../README.md) | Overview, tool list, philosophy |
| [HOW_IT_WORKS.md](../HOW_IT_WORKS.md) | Architecture and portable setup detail |
| [CONNECTING_ASSISTANTS.md](CONNECTING_ASSISTANTS.md) | Wire Claude / Grok / Codex / Gemini / REST |
| [CONTEXT_BANK_V2.2.md](CONTEXT_BANK_V2.2.md) | Briefs, pins, conflicts, write policy |
| [UPGRADE_TO_V3.md](UPGRADE_TO_V3.md) | Upgrade an existing install to the newest 3.x |
| [MIGRATION.md](../MIGRATION.md) | Older upgrades and data safety |
| [AI_INSTALL_PROMPTS.md](AI_INSTALL_PROMPTS.md) | Let an AI drive install/migrate |

---

## Branch policy (maintainers)

- **`master`** — only active line. MemoryBrain 3.x lives here.  
- Feature work → short-lived branches → merge to `master` → delete branch.  
- Release tags: `v3.1.0`, `v3.0.0`, `v2.5.0`, etc.  
- Historical branches (`feature/memorybrain-2.0`, etc.) are removed once fully merged.
