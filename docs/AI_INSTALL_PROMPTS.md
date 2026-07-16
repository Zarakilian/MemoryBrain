# AI-Assisted Install & Migration Prompts

Copy-paste prompts for having **any AI assistant** (Claude, Gemini, ChatGPT,
Copilot, a local model — anything that can run shell commands or guide you
through them) perform a MemoryBrain install or a v0.5.x → v2.0.0 migration
safely.

The prompts are deliberately strict. They assume nothing about the model:
every command, every expected output, and every stop condition is spelled
out in the prompt itself, so the assistant needs no prior knowledge of
MemoryBrain and no web access.

**How to use:** copy the entire fenced block, paste it as your first message
to the assistant, and answer its questions. If your assistant cannot execute
commands, it will read you each command to run and ask you to paste the
output back.

---

## Prompt 1 — Fresh install

~~~text
You are performing a supervised installation of MemoryBrain, a local,
single-user memory service that runs in Docker on my machine. Follow these
rules without exception:

RULES
1. Work one numbered step at a time. Show me the exact command before it
   runs, then run it (or ask me to run it and paste the output).
2. After every command, compare the real output against the EXPECT note.
   If it does not match, STOP. Report what differs. Do not improvise fixes,
   do not retry with modified commands, do not skip ahead. Wait for me.
3. Never delete, overwrite, or modify any file or Docker volume that
   already exists, except the specific files these steps create.
4. Do not invent commands, flags, URLs, or file paths that are not in
   these instructions or in this repository's README.md / HOW_IT_WORKS.md.
5. Do not send any data anywhere except localhost and the official
   sources named here (github.com, Docker Hub, Ollama model registry).
6. If I have an API key or secret, it goes only into the local .env file.
   Never print a secret's value, never commit it, never put it anywhere else.
7. End with the FINAL REPORT exactly as specified.

STEPS
1. Preconditions. Verify each; report all four versions:
     docker --version        (need Docker with the compose plugin)
     docker compose version
     git --version
     python3 --version       (3.8+; on Windows try: python --version)
   EXPECT: all four print versions. If Docker is missing or the daemon is
   not running, STOP and tell me what to install/start first.
2. Clone:
     git clone https://github.com/Zarakilian/MemoryBrain ~/memorybrain
     cd ~/memorybrain
   EXPECT: clone completes; directory contains docker-compose.yml, brain/,
   cli/, README.md. (If I already chose a different directory, use mine.)
3. Environment file:
     cp .env.example .env
   Then ask me: (a) do I want an API key set (BRAIN_API_KEY — any random
   string, recommended), and (b) which AI provider — Ollama (default,
   local, no key) / Gemini / OpenAI. Edit .env accordingly. Show me the
   final .env with any secret values masked.
4. One-command setup:
     python3 cli/brain.py setup --auto-detect
   EXPECT: it reports Docker running, containers started, models pulled
   (first Ollama model pull can take several minutes), MCP registered,
   hooks and skills installed. If any single check fails, STOP and show
   me the exact failing line.
5. Verify service health:
     curl -s localhost:7741/readiness
   EXPECT: JSON containing "ready": true. If "ready" is false, show me
   which check inside "checks" is not "ok" and STOP.
6. Verify end-to-end write and read:
     curl -s -X POST localhost:7741/ingest/note \
       -H "Content-Type: application/json" \
       -d '{"content":"MemoryBrain installed and verified","project":"setup-test","tags":["setup"]}'
   EXPECT: JSON reply containing an "id".
   (If BRAIN_API_KEY was set, add:  -H "X-Brain-Key: <the key>" )
7. Verify the web UI: open http://localhost:7741/ui in a browser —
   EXPECT the Atlas interface with the "setup-test" memory visible in the
   Stream. Also open http://localhost:7741/ui/doctor and confirm every
   line reads PASS.

FINAL REPORT — print exactly this, filled in:
   MemoryBrain install: SUCCESS or FAILED-AT-STEP-<n>
   readiness: <the JSON from step 5>
   test memory id: <id from step 6>
   doctor: ALL PASS or <the failing lines>
   next steps for the human: tag real projects by creating a .brainproject
   file in each project root (see README "Project detection").
~~~

---

## Prompt 2 — Backup + migrate v0.5.x → v2.0.0

~~~text
You are performing a supervised upgrade of an EXISTING MemoryBrain v0.5.x
installation to v2.0.0 on my machine. My stored memories are irreplaceable.
Follow these rules without exception:

RULES
1. Work one numbered step at a time. Show every command before running it;
   compare real output against the EXPECT note; on any mismatch STOP,
   report, and wait for me. Never improvise.
2. THE BACKUP (step 2) IS NON-NEGOTIABLE. If it fails, the migration does
   not proceed. Never run any later step without a verified backup.
3. Never delete or modify existing data: no rm, no volume removal, no
   `docker compose down -v`, no pruning — none of those appear in these
   steps and must never be suggested.
4. Do not invent commands or flags. Everything you need is here.
5. End with the FINAL REPORT exactly as specified.

STEPS
1. Record the starting state. From the repo directory:
     docker compose exec brain python -c "import sqlite3; print(sqlite3.connect('/app/data/brain.db').execute('SELECT COUNT(*) FROM memories').fetchone()[0])"
   Write this number down as MEMORY_COUNT_BEFORE. EXPECT: an integer.
2. Backup (data volume + config). Linux/macOS shell:
     docker compose stop brain
     docker run --rm -v memorybrain_brain_data:/data -v "$PWD":/backup alpine tar czf /backup/brain-backup-$(date +%Y%m%d).tar.gz /data
     cp .env .env.backup-$(date +%Y%m%d)
     docker compose start brain
   Windows PowerShell equivalent:
     docker compose stop brain
     docker run --rm -v memorybrain_brain_data:/data -v "${PWD}:/backup" alpine tar czf /backup/brain-backup-$(Get-Date -Format yyyyMMdd).tar.gz /data
     Copy-Item .env ".env.backup-$(Get-Date -Format yyyyMMdd)"
     docker compose start brain
   Then VERIFY the backup exists and is not tiny:
     ls -l brain-backup-*.tar.gz     (PowerShell: Get-Item brain-backup-*.tar.gz)
   EXPECT: a .tar.gz dated today, size at least several hundred KB.
   If the volume name differs, find it with: docker volume ls
3. Upgrade:
     git pull
     docker compose build brain
     docker compose up -d
   EXPECT: build succeeds; containers start. The v2 migration runs itself
   on first startup (additive schema changes + copying embeddings out of
   the legacy Chroma directory, which is opened read-only and left intact).
4. Verify subsystems:
     curl -s localhost:7741/readiness
   EXPECT: "ready": true and "vector_store": "ok".
5. Verify zero data loss:
     docker compose exec brain python -c "import sqlite3; c=sqlite3.connect('/app/data/brain.db'); print('memories:', c.execute('SELECT COUNT(*) FROM memories').fetchone()[0]); print('vectors:', c.execute('SELECT COUNT(*) FROM vec_memories').fetchone()[0])"
   EXPECT: memories == MEMORY_COUNT_BEFORE exactly. vectors equal or within
   a few of memories. If memories differs AT ALL, STOP — do not touch
   anything further — and tell me the rollback options from MIGRATION.md.
   If vectors < memories by more than a few, run:
     curl -s -X POST localhost:7741/admin/backfill-vectors
   (add -H "X-Brain-Key: <key>" if BRAIN_API_KEY is set) and re-check.
6. Build the memory graph (one-time):
     curl -s -X POST localhost:7741/admin/rebuild-graph
   (same X-Brain-Key note.) EXPECT: JSON with an edge count.
7. Verify the UI: open http://localhost:7741/ui — EXPECT the Atlas
   (Stream / Constellation / Chronicle). Open http://localhost:7741/ui/doctor —
   EXPECT every line PASS.

FINAL REPORT — print exactly this, filled in:
   MemoryBrain migration: SUCCESS or FAILED-AT-STEP-<n>
   backup file: <name and size>   .env backup: <name>
   memories before: <n>   after: <n>   vectors: <n>   edges: <n>
   readiness: ready=<true|false>
   doctor: ALL PASS or <failing lines>
   rollback paths (unchanged, both available): MEMORYBRAIN_VECTOR_BACKEND=chroma
   in .env for the legacy vector store, or git checkout v0.5.0 + rebuild;
   the tar backup restores everything if ever needed.
~~~

---

## Notes for humans

- Both prompts are self-contained: the assistant needs no repository
  context, no web access, and no memory of MemoryBrain to follow them.
- The EXPECT / STOP discipline is the point. A model that cannot follow it
  should not be driving your migration — do it by hand with MIGRATION.md.
- Assistants without shell access can still be used: they will dictate each
  command and interpret the output you paste back. The rules hold either way.
