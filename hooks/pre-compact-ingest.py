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
import http.client
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


# brain setup records the install folder beside the installed hooks
HOME_FILE = Path(__file__).resolve().parent / "memorybrain-home"


def brain_key() -> str:
    """BRAIN_API_KEY from the environment, else from the install's .env
    (MEMORYBRAIN_DIR, else the folder memorybrain-home names). Never logged."""
    key = os.getenv("BRAIN_API_KEY", "").strip()
    if key:
        return key
    home = os.getenv("MEMORYBRAIN_DIR", "").strip()
    if not home:
        try:
            home = HOME_FILE.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    try:
        lines = (Path(home) / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        s = line.strip()
        if s.startswith("BRAIN_API_KEY") and "=" in s:
            name, value = s.split("=", 1)
            if name.strip() == "BRAIN_API_KEY":
                return value.strip().strip('"').strip("'")
    return ""


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
    return _post(content, project, trigger) is not None


def _post(content: str, project: str, trigger: str):
    """The brain's reply ({} when it is not JSON), or None when nothing was stored."""
    payload = json.dumps({"content": content, "project": project,
                          "source": f"pre-compact:{trigger}"}).encode("utf-8")
    headers = {"Content-Type": "application/json", "X-Brain-Client": "hook"}
    api_key = brain_key()
    if api_key:
        headers["X-Brain-Key"] = api_key
    req = urllib.request.Request(f"{BRAIN_URL}/ingest/session", data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        _log(f"brain answered HTTP {e.code}; session not ingested")
        return None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        _log("brain not running, timed out, or the reply was cut off; session not ingested")
        return None
    try:
        result = json.loads(raw)
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    _log(f"session ingested, id={result.get('id', '?')}")
    return result


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
    project = detect_project(cwd)
    handover = recent_handover(cwd, time.time())
    if handover:
        reply = _post(handover, project, trigger)
        if reply is None:
            return 0
        if not reply.get("duplicate"):
            update_memory_timestamp(cwd)
            return 0
        # that handover is already stored (an earlier compaction today): the
        # conversation since then is what is new
    content = transcript_tail(Path(hook["transcript_path"]), trigger) \
        if hook.get("transcript_path") else ""
    if not content:
        if not handover:
            _log("nothing to ingest (no recent handover, no transcript text)")
        return 0
    if post_session(content, project, trigger):
        update_memory_timestamp(cwd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
