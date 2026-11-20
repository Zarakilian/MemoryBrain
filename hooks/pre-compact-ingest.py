#!/usr/bin/env python3
"""
MemoryBrain capture hook: before a compaction, and at the end of a session.

Claude Code runs this before it compacts the context, with JSON on stdin:
{session_id, transcript_path, cwd, trigger}. The hook stores what is about
to be compacted as a session memory:

1. the newest HANDOVER-*.md in the working folder, if written in the last
   12 hours, else
2. the tail of the transcript: the text of the last 40 user and assistant
   messages (tool calls and results skipped), at most 20,000 characters.

Wired to SessionEnd as well ({hook_event_name: "SessionEnd", reason, ...}), it
is the safety net for a session that ends without a log: the transcript tail
is stored, unless the session was only a few messages long, and unless a
session log reached the brain in the last three hours (the brain checks, so
a session that ran log-everything is never stored twice).

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
SESSION_END_MIN_MESSAGES = 6      # a shorter session has nothing worth keeping
SESSION_END_QUIET_MINUTES = 180   # a session log this recent already covers it
SUMMARY_ASK_CHARS = 160


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
    return slug[:64].strip("-") or "unknown"     # the session hook cuts the same way


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


def transcript_tail(path: Path, trigger: str, when: str = "compaction") -> str:
    """The last 40 text messages of a Claude Code JSONL transcript, formatted."""
    return transcript_capture(path, trigger, when)[0]


def transcript_capture(path: Path, trigger: str, when: str = "compaction") -> tuple:
    """(the formatted tail, how many text messages the transcript holds, the
    last thing the user asked). ("", 0, "") when there is nothing to read."""
    messages = []
    last_ask = ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", 0, ""
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("isMeta"):
            continue  # isMeta lines are injected context, not the conversation
        role, text = _message_text(event)
        if text:
            if role == "user":
                last_ask = text
            if len(text) > MESSAGE_CHARS:
                text = text[:MESSAGE_CHARS] + " [...]"
            messages.append(f"[{role}] {text}")
    if not messages:
        return "", 0, ""
    body = "\n\n".join(messages[-TAIL_MESSAGES:])[-TAIL_CHARS:]
    return (f"Session transcript tail, captured automatically at {when} ({trigger}).\n\n"
            + body, len(messages), last_ask)


def capture_summary(when: str, last_ask: str) -> str:
    """One line for the brief, so an automatic capture never reads like a log
    someone wrote: it says what it is, and the last thing the user asked."""
    ask = " ".join((last_ask or "").split())[:SUMMARY_ASK_CHARS]
    head = ("Auto-captured at session end, no session log was written"
            if when == "session end" else "Auto-captured at compaction")
    return f"{head}: {ask}" if ask else head


def post_session(content: str, project: str, trigger: str) -> bool:
    return _post(content, project, trigger) is not None


def _post(content: str, project: str, trigger: str, source: str = "", summary: str = "",
          quiet_minutes: int = 0):
    """The brain's reply ({} when it is not JSON), or None when nothing was stored."""
    body = {"content": content, "project": project,
            "source": source or f"pre-compact:{trigger}"}
    if summary:
        body["summary"] = summary
    if quiet_minutes:
        body["quiet_minutes"] = quiet_minutes
    payload = json.dumps(body).encode("utf-8")
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
    if result.get("skipped"):
        _log(f"not stored: {result['skipped']}")
    else:
        _log(f"session ingested, id={result.get('id', '?')}")
    return result


def update_memory_timestamp(cwd: Path) -> None:
    """Stamp this project's MEMORY.md with the MemoryBrain Last Active time."""
    project_hash = re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))
    mem_file = Path.home() / ".claude" / "projects" / project_hash / "memory" / "MEMORY.md"
    if not mem_file.exists():
        return
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    stamp_memory_file(mem_file, ts)


def stamp_memory_file(mem_file: Path, ts: str) -> None:
    """Set the Last Active line, keeping the file's own line endings (the two
    hooks used to flip a Windows file between CRLF and LF)."""
    marker = "**MemoryBrain Last Active:**"
    with open(mem_file, encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    line = re.compile(r"^\*\*MemoryBrain Last Active:\*\*[^\r\n]*", re.M)
    if line.search(text):
        text = line.sub(lambda m: f"{marker} {ts}", text, count=1)
    else:
        text = f"{marker} {ts}{nl}{nl}" + text
    with open(mem_file, "w", encoding="utf-8", newline="") as f:
        f.write(text)


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
    project = detect_project(cwd)
    if hook.get("hook_event_name") == "SessionEnd":
        return _session_end(hook, cwd, project)
    trigger = str(hook.get("trigger") or "auto")
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
    content, _count, last_ask = transcript_capture(Path(hook["transcript_path"]), trigger) \
        if hook.get("transcript_path") else ("", 0, "")
    if not content:
        if not handover:
            _log("nothing to ingest (no recent handover, no transcript text)")
        return 0
    if _post(content, project, trigger,
             summary=capture_summary("compaction", last_ask)) is not None:
        update_memory_timestamp(cwd)
    return 0


def _session_end(hook: dict, cwd: Path, project: str) -> int:
    """The session is over. Store its tail unless it was too short to matter;
    the brain skips it when a session log was written in the last three hours."""
    reason = re.sub(r"[^a-z0-9_-]", "", str(hook.get("reason") or "other").lower())[:40] or "other"
    content, count, last_ask = transcript_capture(Path(hook["transcript_path"]), reason,
                                                  "session end") \
        if hook.get("transcript_path") else ("", 0, "")
    if not content or count < SESSION_END_MIN_MESSAGES:
        _log("nothing to ingest at session end (a short session, or no transcript text)")
        return 0
    _post(content, project, reason, source=f"session-end:{reason}",
          summary=capture_summary("session end", last_ask),
          quiet_minutes=SESSION_END_QUIET_MINUTES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
