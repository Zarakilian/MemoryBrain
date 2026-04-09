#!/usr/bin/env python3
"""
MemoryBrain pre-compact hook.
Called by Claude Code before context compaction.
POSTs the most recent handover content to the brain as a session memory.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

BRAIN_URL = os.getenv("MEMORYBRAIN_URL", "http://localhost:7741")
CWD = Path(os.getenv("CLAUDE_CWD", os.getcwd()))

# H3: Validate BRAIN_URL is localhost-only (prevent SSRF via env manipulation)
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
from urllib.parse import urlparse as _urlparse
_parsed = _urlparse(BRAIN_URL)
if _parsed.hostname not in _ALLOWED_HOSTS:
    print(f"[memorybrain] BRAIN_URL must be localhost — refusing to connect to {BRAIN_URL}", file=sys.stderr)
    sys.exit(0)


def detect_project(cwd: Path) -> str:
    brain_file = cwd / ".brainproject"
    if brain_file.exists():
        return brain_file.read_text().strip()
    # Heuristic: last meaningful path segment
    parts = [p for p in cwd.parts if p not in ("", "/", "mnt", "c", "git")]
    return parts[-1].lower() if parts else "unknown"


def update_memory_timestamp(cwd: Path) -> None:
    """Stamp this project's MEMORY.md with the current MemoryBrain Last Active time."""
    import re
    project_hash = re.sub(r'[^a-zA-Z0-9]', '-', str(cwd))
    mem_file = Path.home() / ".claude" / "projects" / project_hash / "memory" / "MEMORY.md"
    if not mem_file.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = "**MemoryBrain Last Active:**"
    text = mem_file.read_text()
    if marker in text:
        text = re.sub(r'\*\*MemoryBrain Last Active:\*\*.*', f"{marker} {ts}", text)
    else:
        text = f"{marker} {ts}\n\n" + text
    mem_file.write_text(text)


def post_session(content: str, project: str):
    payload = json.dumps({
        "content": content,
        "project": project,
        "source": f"pre-compact:{datetime.now(timezone.utc).isoformat()}",
    }).encode()
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("BRAIN_API_KEY")
    if api_key:
        headers["X-Brain-Key"] = api_key
    req = urllib.request.Request(
        f"{BRAIN_URL}/ingest/session",
        data=payload,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            print(f"[memorybrain] Session ingested — id={result.get('id', '?')}", file=sys.stderr)
            update_memory_timestamp(CWD)
    except (urllib.error.URLError, TimeoutError):
        print("[memorybrain] Brain not running or timed out — session not ingested", file=sys.stderr)


def main():
    # Try reading handover from stdin first
    content = ""
    if not sys.stdin.isatty():
        content = sys.stdin.read().strip()

    # Fall back to most recent handover file in CWD
    if not content:
        handover_files = sorted(CWD.glob("HANDOVER-*.md"), reverse=True)
        if handover_files:
            content = handover_files[0].read_text()

    if not content:
        print("[memorybrain] No content to ingest", file=sys.stderr)
        return

    project = detect_project(CWD)
    post_session(content, project)


if __name__ == "__main__":
    main()
