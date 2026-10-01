---
name: map-project-files
description: Show the authoritative files for this project from MemoryBrain's workspace index (no filesystem scanning) — triggered by /map-project-files or "map project files"
version: 3.0.0
disable-model-invocation: false
---

# Map Project Files

Since MemoryBrain 2.5 the file map is not a hand-written memory. It is the live
workspace index, kept current by `brain scan` and joined to memories by `file_ref` edges.

## Steps

1. Detect the project slug: read `.brainproject` in the working directory if present,
   otherwise use the last meaningful path segment.
2. Call `mcp__memorybrain__brain_admin` with `action="get_project_files"` and
   `args={"project": "<slug>", "sort": "ref_degree", "limit": 30}`. (With
   `MEMORYBRAIN_TOOLS=full` the same call is the `get_project_files` tool.)
   The top rows are the files most memories refer to. That is the file map.
   For one file, `get_file_context(path=…)` is a core tool.
3. If the result is empty, the workspace has not been scanned on this machine. Tell the user to run:

   ```
   python <path-to-MemoryBrain>\cli\brain.py scan --root "C:\work\repos" --label git
   ```

   and, the first time, `--init` then `--apply workspace-map.proposed.json`.
4. Do not write a `file-map` reference memory. The index supersedes it.
