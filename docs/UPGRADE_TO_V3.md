# Upgrade MemoryBrain to 3.x

**Purpose:** move a running MemoryBrain (2.5 or any 3.x) to the newest 3.x release without losing a memory.
**Audience:** you at a terminal, or an AI assistant. For an AI, paste [Prompt 3](AI_INSTALL_PROMPTS.md#prompt-3-upgrade-an-existing-install-to-3x): it holds these steps plus the rules an AI must keep.
**Done when:** the running brain reports the new version, `/readiness` says `"ready": true`, and the count printed after the upgrade is at least the count before it.
**Last verified:** 2026-10-01, on a real 3.0.0 to 3.1.0 upgrade.
**Time:** 10 to 15 minutes. From 2.x, a background re-embed follows (about a minute per 25 memories). Search keeps working while it runs.
**You need:** Docker running, Git, Python 3 on the host, and the folder of your live install.
**Out of scope:** first installs ([GETTING_STARTED.md](GETTING_STARTED.md)), upgrades from 0.5.x ([MIGRATION.md](../MIGRATION.md) first).

One command does the work: `python3 cli/brain.py upgrade`. It refuses the unsafe states listed in step 2, backs the data volume up before it touches anything, and counts your memories before and after. Most of the time below is reading its output.

The examples use `~/memorybrain` for the install folder and `~/memorybrain-backups` for backups. Use your own names. On Windows, run the commands in PowerShell, use `python` where this says `python3`, and `curl.exe` where it says `curl`.

## 0. Find your live install (changes nothing)

Docker Compose names the data volume after the install folder: a folder called `memorybrain` owns the volume `memorybrain_brain_data`. Ask Docker which folder that is:

```bash
docker compose ls --all
```

**Expect:** a row named after your install (usually `memorybrain`) whose `CONFIG FILES` path ends in `docker-compose.yml` inside the live folder. Run every step below from that folder.
**If wrong:** no row means Compose has never run here, so look for the volume with `docker volume ls` (it is `<folder>_brain_data`). Two MemoryBrain rows mean two brains: stop and work out which one holds your memories.

Then check which history the folder has:

```bash
cd ~/memorybrain
git log --max-parents=0 --format=%s
```

**Expect:** `MemoryBrain 2.5.0: application only, clean start`. Skip to step 2.
**If wrong:** anything else is the history the clean start replaced. Do step 1.

## 1. Old history only: rename the folder and clone again

The new clone takes the **old folder's name**, so it finds the same volume. A clone under any other name would start an empty brain next to your real one.

```bash
cd ~
mv memorybrain memorybrain-old
git clone https://github.com/Zarakilian/MemoryBrain.git memorybrain
cp memorybrain-old/.env memorybrain/.env
```

PowerShell: `Rename-Item memorybrain memorybrain-old`, the same `git clone`, then `Copy-Item memorybrain-old\.env memorybrain\.env`.

**Expect:** the clone completes and the new folder has a `.env`.
**If wrong:** a refused rename means something has a file open in that folder. Close terminals and editors that sit in it, then try again.

Renaming does not stop the running brain or touch its volume. **Never push from `memorybrain-old`.** Keep it only as a rollback path, and delete it once the new version has run for a while.

## 2. Upgrade with one command

From the live folder (skip `git pull` if you just cloned):

```bash
cd ~/memorybrain
git pull
python3 cli/brain.py upgrade
```

It refuses to start if the clone is old, if `.env` is missing, if `.env` names a cloud key without `MEMORYBRAIN_PROVIDER`, or if this folder has no brain volume. Then it counts memories, stops the brain, backs the volume up to `~/memorybrain-backups`, rebuilds, starts, waits for readiness, counts again, and reinstalls hooks and skills. A hook it replaces keeps a `.bak-<date>` copy beside it. A skill you edited is never replaced: the new version is saved beside it as `SKILL.md.new`, for you to merge by hand.

**Use `upgrade`, never `update`, on a live brain.** `brain update` pulls and rebuilds without the backup and without the count check.

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

PowerShell:

```powershell
cd ~\memorybrain
New-Item -ItemType Directory -Force "$HOME\memorybrain-backups" | Out-Null
docker compose stop brain
$stamp = Get-Date -Format yyyyMMdd-HHmm
docker run --rm -v memorybrain_brain_data:/data:ro -v "$HOME\memorybrain-backups:/backup" alpine tar czf "/backup/brain-backup-$stamp.tar.gz" -C /data .
Copy-Item .env "$HOME\memorybrain-backups\env-backup-$stamp"
docker compose build brain
docker compose up -d
```

**Expect:** the `.tar.gz` in `~/memorybrain-backups` is not tiny (several hundred KB or more), and `tar tzf <file>` lists `./brain.db`. Check it before `docker compose build`. (`brain upgrade` checks both for you.)

## 3. Verify

```bash
docker compose exec -T brain cat VERSION
cat VERSION
curl -s localhost:7741/readiness
```

**Expect:** the first two lines print the same version: the running brain is this folder's release. `/readiness` shows `"ready": true` with every check `ok`. From 2.x, `reembed_pending` falls by about 25 a minute while old vectors are rebuilt; search keeps working meanwhile. (`/status` shows the version too, but answers 401 without `-H "X-Brain-Key: …"` when `BRAIN_API_KEY` is set. `/readiness` never needs the key.)
**If wrong:** `"ready": false` is usually an Ollama model that is not pulled yet (see the table). A count that fell means stop and restore (see Rollback).

Then open `http://localhost:7741/ui/doctor`. **Expect:** every line PASS. A plain reload of an open Atlas tab picks up the new UI, because every asset link carries the new build stamp.

## 4. A separate development clone

Only if you also keep a clone for development, apart from the live install:

1. Rename it and clone again, as in step 1. Any folder name works, because a dev clone owns no volume.
2. In the new clone, turn on the hygiene hooks, so every commit and push is checked for machine data:

   ```bash
   git config core.hooksPath .githooks
   ```

3. Copy the old clone's `.local/` folder into the new one. It holds your machine notes and private hygiene patterns, and git ignores it.

**Expect:** `git log --max-parents=0 --format=%s` in the new clone shows the clean-start line. **Never push from the old clone.**

## 5. Check each assistant

| Assistant | What to do |
|---|---|
| Claude Code | Start a new session. The hooks were reinstalled; the session start shows this project's brief only. Agents see 15 core tools, `brain_admin` among them, which runs the rest. |
| Grok | Streamable HTTP carries no client name, so pass `source="grok"`, or send an `X-Brain-Agent: grok` header if your config can. Copy `skills/log-everything/SKILL_GROK.md` over your Grok copy only if you never edited it. |
| Codex | Restart Codex so stdio MCP reloads. Point `~/.codex/AGENTS.md` at `brain_admin` for the less common tools. |
| Gemini | Restart it. The stdio entry is unchanged. |
| Any prompt that calls an old tool by name | It still works. To list every tool again, set `MEMORYBRAIN_TOOLS=full` in `.env`, then `docker compose up -d brain`. |

Merge any `SKILL.md.new` soon: a kept skill may call a tool the core profile hides (for example `get_project_files`), which most clients can only reach through `brain_admin`.

## If it fails

| You see | Check | Fix |
|---|---|---|
| `This clone predates the 2026-09-30 clean start` | `git log --max-parents=0` in this folder | Do step 1 |
| `No .env in …` | Is this the new clone? | Copy `.env` from the old folder |
| `Your .env sets … but not MEMORYBRAIN_PROVIDER` | Which provider your brain really uses | Add `MEMORYBRAIN_PROVIDER=gemini`, `openai` or `ollama` to `.env`, run again |
| `No volume memorybrain_brain_data …` | `docker compose ls --all`, `docker volume ls` | Run from the folder whose name owns the volume, or set `COMPOSE_PROJECT_NAME` |
| `could not count memories: …` | Is Docker running? Is the volume readable? | Fix what the message names, run again. Nothing was stopped |
| `Failed while stopping the brain` | `docker compose ps` | Fix it, run again. Nothing was backed up or changed |
| `Failed while backing up the volume` | Can Docker write to the backup folder? | Fix the path or permissions, `docker compose start brain`, run again |
| `The backup did not reach …` | Is Docker running, and can it see the backup folder? | Fix the path, `docker compose start brain`, run again |
| `The backup … has no brain.db in it` (or an empty or unreadable one) | `tar tzf` the file it names | Do not rebuild. `docker compose start brain`, find out why the volume holds no database, run again |
| `Failed while building the new image` | `docker compose build brain` output | Fix it, then `docker compose up -d brain` starts the old image again |
| `Failed while starting the brain` | `docker compose logs brain` | Fix it, then `docker compose up -d brain`. The backup it names is your way back |
| `Memory count fell from …` | Nothing else. Stop here | Rollback, below |
| `running but not ready` | `curl -s localhost:7741/readiness` | Usually `docker compose exec ollama ollama pull embeddinggemma` |
| The Constellation shows 2D only | Is WebGL on in this browser? | After a graphics reset, Chrome can block 3D until the browser restarts. Restart it |

## From 3.1 to 3.2

- **The first start scrubs stored secrets.** Secrets written before 3.0 are redacted once, after a copy of the database lands in `/app/data/backups/` inside the volume (only when something changes). The brain log names the rows' tables and the copy: `docker compose logs brain | grep "Redaction scan"`.
- **A key now covers MCP.** With `BRAIN_API_KEY` set, `/sse`, `/messages/` and `/mcp` need it. Register Claude Code again with the key header and add it to other HTTP clients ([GETTING_STARTED.md](GETTING_STARTED.md#optional-api-key)). No key set: nothing changes.
- **The hooks find the key themselves.** `brain upgrade` reinstalls them and records the install folder beside them, so they read `BRAIN_API_KEY` from its `.env`.

## Rollback

### The code, between two 3.x releases

There is no old folder to go back to. In the live folder, find the commit of the release you came from and run it detached:

```bash
cd ~/memorybrain
git log --oneline -15
git switch --detach <commit of the previous release>
docker compose up -d --build brain
```

Your memories stay as they are. Go back to the newest release later with `git switch master`, then `python3 cli/brain.py upgrade`. Restore the data as well (below) only if the count fell.

### The data

From the new folder, stop the brain, then put the backup back. The first command removes the new database files so a newer WAL file cannot be replayed onto the older copy:

```bash
docker compose stop brain
docker run --rm -v memorybrain_brain_data:/data -v ~/memorybrain-backups:/backup alpine \
  sh -c "rm -f /data/brain.db /data/brain.db-wal /data/brain.db-shm && tar xzf /backup/<your-backup>.tar.gz -C /data"
cd ~/memorybrain-old
docker compose -p memorybrain up -d --build
```

**This replaces the database with the backup.** Memories written after the backup are lost. `-p memorybrain` makes the old folder use the same volume despite its new name.

Coming back from 3.x to 2.x, also put the 2.x hooks and skills back: in `~/.claude/hooks` and in each folder under `~/.claude/skills`, copy the newest `*.bak-<date>` file over the file it was made from. The v3 versions call `brain_admin` and `record_correction`, which 2.x does not have. Between two 3.x releases, the hooks and skills need nothing.

A faster source than the tar, if the brain itself is fine: before each migration the runner copies the database to `/app/data/backups/` inside the volume.

## What I have not verified

- The Grok and Codex config keys for custom headers. Check your client's own MCP docs before adding `X-Brain-Agent`.
- Timings come from one machine. A large brain takes longer to back up and to re-embed.
- The rollback was written from the backup format `brain upgrade` produces, not rehearsed on a real brain.
- `docker compose ls --all` was run on Docker with the Compose v2 plugin on Windows, where `CONFIG FILES` shows a Windows path. An older standalone `docker-compose` may not have `ls`.
