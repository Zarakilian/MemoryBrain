# Upgrade MemoryBrain to 3.0

**If your clone was made before 2026-09-30, rename that folder first and clone the repo again. The old history must never be pushed.**

**Purpose:** move an existing 2.x brain to 3.0 without losing a memory.
**Audience:** anyone running MemoryBrain in Docker, comfortable with a terminal.
**Done when:** `/readiness` says `"ready": true`, the memory count matches the count before the upgrade, and `reembed_pending` is falling towards 0.
**Last verified:** 2026-10-01
**Time:** about 10 minutes, then a background re-embed (about 1 minute per 25 memories) you do not have to wait for.
**You need:** the folder of your live install, Docker running, Python 3 on the host.
**Out of scope:** first installs ([GETTING_STARTED.md](GETTING_STARTED.md)), upgrades from 0.5.x ([MIGRATION.md](../MIGRATION.md) first).

The examples use `~/memorybrain` for the install folder and `~/memorybrain-backups` for backups. Use your own folder names.

## Why the folder name matters

Docker Compose names the data volume after the folder: a folder called `memorybrain` owns the volume `memorybrain_brain_data`. A new clone with a different folder name starts an empty brain next to your real one. So the new clone takes the **old folder's name**, and the old folder gets a new one.

## 1. Rename the old clone and clone again

Only if your clone predates 2026-09-30. Check from inside it:

```bash
git log --max-parents=0 --format=%s
```

**Expect:** `MemoryBrain 2.5.0: application only, clean start`. If you see that, skip to step 2.
**If wrong:** anything else is the old history. Rename and re-clone:

```bash
cd ~
mv memorybrain memorybrain-old
git clone https://github.com/Zarakilian/MemoryBrain.git memorybrain
cp memorybrain-old/.env memorybrain/.env
```

PowerShell: `Rename-Item memorybrain memorybrain-old`, the same `git clone`, then `Copy-Item memorybrain-old\.env memorybrain\.env`.

Renaming the folder does not stop the running brain or touch its volume. **Never push from `memorybrain-old`.** Keep it only as a rollback path, then delete it once 3.0 has run for a while.

## 2. Upgrade with one command

From the new clone:

```bash
cd ~/memorybrain
python3 cli/brain.py upgrade
```

It refuses to start if the clone is old, if `.env` is missing, or if this folder has no brain volume. Then it counts memories, stops the brain, backs the volume up to `~/memorybrain-backups`, rebuilds, starts, waits for readiness, counts again, and reinstalls hooks and skills. A hook it replaces keeps a `.bak-<date>` copy beside it. A skill you edited is never replaced: the new version is saved beside it as `SKILL.md.new`, for you to merge by hand.

**Expect:** lines like `✅ 1234 memories in memorybrain_brain_data`, `✅ Backup: …tar.gz`, `✅ 1234 memories after the upgrade (before: 1234)`, `reembed_pending: …`, `✅ Upgrade complete.`
**If wrong:** it stops at the first problem and says which. Nothing after the failing step ran. The table at the end covers each message.

Use `--backup-dir <folder>` to put the backup elsewhere. It must be outside the repo.

### The same steps by hand

Only if you cannot run `brain upgrade`. The brain must be stopped for the backup, because a copy of a live WAL database can be torn.

```bash
cd ~/memorybrain
mkdir -p ~/memorybrain-backups
docker compose stop brain
docker run --rm -v memorybrain_brain_data:/data:ro -v ~/memorybrain-backups:/backup alpine \
  tar czf /backup/brain-backup-$(date +%Y%m%d-%H%M).tar.gz -C /data .
cp .env ~/memorybrain-backups/env-backup-$(date +%Y%m%d)
docker compose build brain
docker compose up -d
```

**Expect:** the `.tar.gz` in `~/memorybrain-backups` is not tiny (several hundred KB or more). Check it before `docker compose build`.

## 3. Verify

```bash
curl -s localhost:7741/readiness
docker compose exec brain python -c "import sqlite3; print(sqlite3.connect('/app/data/brain.db').execute('SELECT COUNT(*) FROM memories').fetchone()[0])"
```

**Expect:** `"ready": true` with every check `ok`, and the same count as before. `reembed_pending` falls by about 25 a minute: old vectors are rebuilt with 3.0's prompts and chunks in the background, and search keeps working while it runs.
**If wrong:** a lower count means stop and restore (see Rollback). `"ready": false` is usually an Ollama model that is not pulled yet.

Then open `http://localhost:7741/ui/doctor`. **Expect:** every line PASS.

## 4. Check each assistant

| Assistant | What to do |
|---|---|
| Claude Code | Start a new session. The hooks were reinstalled; the session start now shows this project's brief only. Agents see 15 core tools plus `brain_admin`. |
| Grok | Streamable HTTP carries no client name, so pass `source="grok"`, or send an `X-Brain-Agent: grok` header if your config can. Copy `skills/log-everything/SKILL_GROK.md` over your Grok copy only if you never edited it. |
| Codex | Restart Codex so stdio MCP reloads. Point `~/.codex/AGENTS.md` at `brain_admin` for the less common tools. |
| Gemini | Restart it. The stdio entry is unchanged. |
| Any prompt that calls an old tool by name | It still works. To list every tool again, set `MEMORYBRAIN_TOOLS=full` in `.env`, then `docker compose up -d brain`. |

## If it fails

| You see | Check | Fix |
|---|---|---|
| `This clone predates the 2026-09-30 clean start` | `git log --max-parents=0` in this folder | Do step 1 |
| `No .env in …` | Is this the new clone? | Copy `.env` from the old folder |
| `No volume memorybrain_brain_data …` | `docker volume ls` | Run from the folder whose name owns the volume, or set `COMPOSE_PROJECT_NAME` |
| `The backup did not reach …` | Is Docker running on this machine, and can it see the backup folder? | Fix the path, `docker compose start brain`, run again |
| `Failed while building the new image` | `docker compose build brain` output | Fix it, then `docker compose up -d brain` starts the old image again |
| `Memory count fell from …` | Nothing else. Stop here | Rollback, below |
| `running but not ready` | `curl -s localhost:7741/readiness` | Usually `docker compose exec ollama ollama pull embeddinggemma` |

## Rollback

From the new folder, stop the brain, then put the backup back. The first command removes the 3.0 database files so a newer WAL file cannot be replayed onto the older copy:

```bash
docker compose stop brain
docker run --rm -v memorybrain_brain_data:/data -v ~/memorybrain-backups:/backup alpine \
  sh -c "rm -f /data/brain.db /data/brain.db-wal /data/brain.db-shm && tar xzf /backup/<your-backup>.tar.gz -C /data"
cd ~/memorybrain-old
docker compose -p memorybrain up -d --build
```

**This replaces the database with the backup.** Memories written after the backup are lost. `-p memorybrain` makes the old folder use the same volume despite its new name.

## What I have not verified

- The Grok and Codex config keys for custom headers. Check your client's own MCP docs before adding `X-Brain-Agent`.
- Timings come from one machine. A large brain takes longer to back up and to re-embed.
- The rollback was written from the backup format `brain upgrade` produces, not rehearsed on a real 3.0 brain.
