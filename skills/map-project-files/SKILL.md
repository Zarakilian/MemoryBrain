---
name: map-project-files
description: Discover and save the high-priority documentation files for this project to MemoryBrain — creates a persistent file map reference memory so future sessions instantly know which files are authoritative without scanning the filesystem each time
version: 1.0.0
disable-model-invocation: false
---

# Map Project Files

Discover and record the high-priority documentation and context files for the current project
in MemoryBrain. Creates (or updates) a `reference` memory that all future sessions can use to
instantly know which files are authoritative for this project — so Claude never has to guess,
scan directories, or re-discover context on every session.

## Why this matters

Without a file map, Claude either reads every `.md` file it finds (slow, noisy) or misses
important ones entirely. With a saved file map, the session startup context includes the exact
paths of authoritative files — Claude can go straight to the right file for the right information.

## When to use

- When setting up a new project with MemoryBrain for the first time
- When important new files are added to a project (run again to update the map)
- When `CLAUDE.md` is updated to reference new files
- Triggered by: `/map-project-files` or when asked to "map project files"

---

## Step 1 — Detect project

Determine the project slug:
- Check for `.brainproject` file in the current working directory → read it for the slug
- If absent, use the last meaningful path segment of CWD (exclude: `mnt`, `c`, `git`, empty segments)

---

## Step 2 — Read CLAUDE.md

Look for a `CLAUDE.md` file in:
1. The current working directory (`./CLAUDE.md`)
2. The user's global Claude config (`~/.claude/CLAUDE.md`)

Read both if they exist. Extract any **explicitly referenced file paths** — lines that mention
specific `.md` files, log files, or directory paths. These are files the project owner has
intentionally declared as authoritative and should be treated as highest priority.

---

## Step 3 — Discover important files

Search the project root (and one level of subdirectories) for files matching these patterns.
Use the Read and Glob tools — do not run shell commands.

**Always include if found:**
- `CLAUDE.md` — Claude's instruction/configuration file for this project
- `MEMORY.md` — project memory index (may also be in `.claude/projects/*/memory/`)
- `README.md` — project overview and entry point

**Include if found (common high-priority naming patterns):**
- `*PROGRESS*`, `*_LOG*`, `*LOG_*` — progress tracking and session logs
- `*HANDOVER*`, `*NOTES*` — context handover documents
- `*PLAN*`, `*ARCH*`, `*DESIGN*` — planning and architecture documents
- `*DEV*`, `HOW_IT_WORKS*` — developer documentation
- Any `.md` file explicitly referenced in `CLAUDE.md`

**Also check:**
- `~/.claude/projects/<hash>/memory/MEMORY.md` — the per-project auto-memory file that MemoryBrain
  stamps with the `Last Active` timestamp at session start. The hash is the CWD path with all
  non-alphanumeric characters replaced by `-`.

---

## Step 4 — Build the file map

For each file discovered, record:
- **Full path** — absolute path or unambiguous relative path from project root
- **Purpose** — one-line description inferred from the filename or the file's first heading/line
- **Source** — one of:
  - `claude.md-referenced` — explicitly named in CLAUDE.md (highest authority)
  - `auto-discovered` — found by pattern matching
  - `both` — named in CLAUDE.md and also matched by pattern

Exclude: auto-generated files, build artefacts, dependency docs, changelogs unless explicitly
referenced in CLAUDE.md.

---

## Step 5 — Save as reference memory

Call `add_memory` with:
- `content`: the structured file map (see format below)
- `type`: `"reference"`
- `project`: the project slug from Step 1
- `tags`: `["project-files", "file-map"]`
- `source`: `"map-project-files"`

**Required content format:**

```
# Project File Map — <project-slug>

High-priority documentation files for this project. These are the authoritative sources
of project context. Use these exact paths — do not scan the filesystem to find them.

## Authoritative Files (declared in CLAUDE.md)
| File | Path | Purpose |
|------|------|---------|
| CLAUDE.md | <full-path> | Claude instructions and session start protocol |
| <filename> | <full-path> | <purpose> |

## Discovered Files
| File | Path | Purpose |
|------|------|---------|
| <filename> | <full-path> | <purpose> |

## Claude Auto-Memory
| File | Path | Purpose |
|------|------|---------|
| MEMORY.md | ~/.claude/projects/<hash>/memory/MEMORY.md | MemoryBrain Last Active timestamp + project index |

Last mapped: <ISO date>
```

---

## Step 6 — Check for existing file map

Before saving, call `search_memory` with query `"project file map"` and `project=<slug>` to check
if a file map already exists.

- **No existing map** → save as new, confirm "File map created."
- **Existing map found** → save the updated version (MemoryBrain deduplicates by content hash, so
  an identical re-run is a no-op). Confirm "File map updated — X files recorded."

---

## Step 7 — Confirm

Tell the user:
- How many files were found, broken down by source (CLAUDE.md-referenced vs auto-discovered)
- Whether this is a new file map or an update to an existing one
- The memory ID returned by `add_memory`
- Reminder:

  > "MemoryBrain will now surface this file map at session start for the `<slug>` project.
  > Future Claude sessions will know exactly where to look for authoritative context without
  > scanning the filesystem. Run `/map-project-files` again any time files are added or moved."
