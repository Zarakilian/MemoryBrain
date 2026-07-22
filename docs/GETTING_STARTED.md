# Getting started with MemoryBrain

This page is for **someone who just found the repo** and wants a working local brain for their AI assistants. No prior project history required.

**Current line of development:** MemoryBrain **2.x** on the default branch `master` (v2.3.0+).  
There is no separate “v2 branch” to checkout — clone `master` and you have MemoryBrain 2.

---

## License

MIT — you may fork and use it freely. See [LICENSE](../LICENSE).

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

If you set `BRAIN_API_KEY` in `.env`, **write and admin** HTTP routes require header `X-Brain-Key`.  
MCP on loopback stays usable without that key (by design). Restart after changing `.env`:

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

Backup once in a while:

```bash
docker compose stop brain
docker run --rm -v memorybrain_brain_data:/data -v "$PWD:/backup" alpine \
  tar czf /backup/brain-backup-$(date +%Y%m%d).tar.gz /data
docker compose start brain
```

(On Windows PowerShell, adjust volume mount syntax; compose project name may prefix the volume as `memorybrain_brain_data`.)

---

## Upgrading from older MemoryBrain (v0.5.x)

1. **Backup the data volume** (see above).  
2. `git pull origin master` and rebuild.  
3. Follow [MIGRATION.md](MIGRATION.md) if you are crossing major storage changes.  
4. Prefer the migration prompt in [AI_INSTALL_PROMPTS.md](AI_INSTALL_PROMPTS.md).

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
| [MIGRATION.md](../MIGRATION.md) | Upgrades and data safety |
| [AI_INSTALL_PROMPTS.md](AI_INSTALL_PROMPTS.md) | Let an AI drive install/migrate |

---

## Branch policy (maintainers)

- **`master`** — only active line. MemoryBrain 2.x lives here.  
- Feature work → short-lived branches → merge to `master` → delete branch.  
- Release tags: `v2.3.0`, etc.  
- Historical branches (`feature/memorybrain-2.0`, etc.) are removed once fully merged.
