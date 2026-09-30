#!/usr/bin/env python3
"""
MemoryBrain pre-compact hook.

Claude Code runs this before it compacts the context, with JSON on stdin:
{session_id, transcript_path, cwd, trigger}. The hook stores what is about
to be compacted as a session memory:

1. the newest HANDOVER-*.md in the working folder, if written in the last
   12 hours, else
2. the tail of the transcript: the text of the last 40 user and assistant
   messages (tool calls and results skipped), at most 20,000 characters.

The stdin JSON itself is never posted.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

BRAIN_URL = os.getenv("MEMORYBRAIN_URL", "http://localhost:7741")
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1"}
HANDOVER_MAX_AGE_S = 12 * 3600
TAIL_MESSAGES = 40
TAIL_CHARS = 20_000
MESSAGE_CHARS = 4_000  # one pasted log or injected summary must not fill the tail


def _log(msg: str) -> None:
    print(f"[memorybrain] {msg}", file=sys.stderr)


def read_hook_input(stream) -> dict:
    """Claude Code's hook JSON; {} when stdin is empty, a terminal or not JSON."""
    try:
        if stream.isatty():
            return {}
        raw = stream.read()
    except (OSError, ValueError):
        return {}
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def detect_project(cwd: Path) -> str:
    """.brainproject in the folder or up to 4 parents, else the folder name."""
    for folder in [cwd, *list(cwd.parents)[:4]]:
        marker = folder / ".brainproject"
        if marker.is_file():
            slug = re.sub(r"[^a-z0-9_-]", "", marker.read_text(encoding="utf-8").strip().lower())
            if slug:
                return slug
    slug = re.sub(r"[^a-z0-9]+", "-", cwd.name.lower()).strip("-")
    return slug[:64] or "unknown"


def recent_handover(cwd: Path, now: float) -> str:
    """Text of the newest HANDOVER-*.md changed in the last 12 hours, or ""."""
    fresh = []
    for path in cwd.glob("HANDOVER-*.md"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if now - mtime <= HANDOVER_MAX_AGE_S:
            fresh.append((mtime, path))
    if not fresh:
        return ""
    return max(fresh)[1].read_text(encoding="utf-8", errors="replace").strip()


def _message_text(event: dict) -> tuple[str, str]:
    """(role, text) for a transcript line; text is "" for tool traffic."""
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    role = message.get("role") or event.get("type") or ""
    if role not in ("user", "assistant"):
        return "", ""
    content = message.get("content", "")
    if isinstance(content, str):
        return role, content.strip()
    if not isinstance(content, list):
        return role, ""
    parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
    return role, "\n".join(t for t in parts if isinstance(t, str) and t).strip()


def transcript_tail(path: Path, trigger: str) -> str:
    """The last 40 text messages of a Claude Code JSONL transcript, formatted."""
    messages = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("isMeta"):
            continue  # isMeta lines are injected context, not the conversation
        role, text = _message_text(event)
        if text:
            if len(text) > MESSAGE_CHARS:
                text = text[:MESSAGE_CHARS] + " [...]"
            messages.append(f"[{role}] {text}")
    if not messages:
        return ""
    body = "\n\n".join(messages[-TAIL_MESSAGES:])[-TAIL_CHARS:]
    return (f"Session transcript tail, captured automatically at compaction ({trigger}).\n\n"
            + body)


def post_session(content: str, project: str, trigger: str) -> bool:
    payload = json.dumps({"content": content, "project": project,
                          "source": f"pre-compact:{trigger}"}).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Brain-Client": "hook"}
    api_key = os.getenv("BRAIN_API_KEY")
    if api_key:
        headers["X-Brain-Key"] = api_key
    req = urllib.request.Request(f"{BRAIN_URL}/ingest/session", data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        _log(f"brain answered HTTP {e.code}; session not ingested")
        return False
    except (urllib.error.URLError, TimeoutError, OSError):
        _log("brain not running or timed out; session not ingested")
        return False
    try:
        result = json.loads(raw)
    except ValueError:
        result = {}
    _log(f"session ingested, id={result.get('id', '?') if isinstance(result, dict) else '?'}")
    return True


def update_memory_timestamp(cwd: Path) -> None:
    """Stamp this project's MEMORY.md with the MemoryBrain Last Active time."""
    project_hash = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))
    mem_file = Path.home() / ".claude" / "projects" / project_hash / "memory" / "MEMORY.md"
    if not mem_file.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    marker = "**MemoryBrain Last Active:**"
    text = mem_file.read_text(encoding="utf-8")
    if marker in text:
        text = re.sub(r"\*\*MemoryBrain Last Active:\*\*.*", f"{marker} {ts}", text)
    else:
        text = f"{marker} {ts}\n\n" + text
    mem_file.write_text(text, encoding="utf-8")


def main(stdin=None) -> int:
    if stdin is None:
        try:
            sys.stdin.reconfigure(encoding="utf-8", errors="replace")  # cp1252 on Windows
        except (AttributeError, ValueError):
            pass
    if urlparse(BRAIN_URL).hostname not in ALLOWED_HOSTS:
        _log(f"MEMORYBRAIN_URL must be localhost; refusing to send to {BRAIN_URL}")
        return 0
    hook = read_hook_input(stdin or sys.stdin)
    cwd = Path(hook.get("cwd") or os.getenv("CLAUDE_PROJECT_DIR") or os.getcwd())
    trigger = str(hook.get("trigger") or "auto")
    content = recent_handover(cwd, time.time())
    if not content and hook.get("transcript_path"):
        content = transcript_tail(Path(hook["transcript_path"]), trigger)
    if not content:
        _log("nothing to ingest (no recent handover, no transcript text)")
        return 0
    if post_session(content, detect_project(cwd), trigger):
        update_memory_timestamp(cwd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
