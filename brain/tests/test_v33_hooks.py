"""3.3 hooks: the session hook starts a file scan when the index is stale, the
capture hook also runs at session end, and a capture is skipped when a session
log was written a moment ago."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models import MemoryEntry
from app.storage import add_memory
from app.workspace import store as ws

for _cand in (Path(__file__).parent.parent.parent / "hooks", Path(__file__).parent.parent / "hooks"):
    if (_cand / "render_brief.py").exists():
        HOOKS = _cand
        break

_spec = importlib.util.spec_from_file_location("capture_hook", HOOKS / "pre-compact-ingest.py")
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

STUB_CURL = """#!/usr/bin/env bash
url=""
for a in "$@"; do case "$a" in http://*) url="$a" ;; esac; done
case "$url" in
  */health) echo '{"status": "ok"}' ;;
  */readiness) echo '{"ready": true}' ;;
  */status) cat "$STUB_STATUS" ;;
  */project-brief*) echo '{"project": "acme", "truncated": []}' ;;
  */next-session*) echo '{"notes": ""}' ;;
  *) echo '{}' ;;
esac
"""
FAKE_BRAIN_CLI = """import pathlib, sys
pathlib.Path(sys.argv[0]).with_name("scan-ran").write_text(" ".join(sys.argv[1:]))
"""


def _run_session_hook(tmp_path, workspace: dict, extra_env=None):
    """Run the installed session hook against a stub brain whose /status says
    `workspace`. Returns the marker the fake `brain.py scan` writes."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(STUB_CURL, encoding="utf-8")
    (bin_dir / "curl").chmod(0o755)
    status = tmp_path / "status.json"
    status.write_text(json.dumps({"version": "9.9.9", "workspace": workspace}), encoding="utf-8")
    install = tmp_path / "install"
    (install / "cli").mkdir(parents=True)
    (install / "cli" / "brain.py").write_text(FAKE_BRAIN_CLI, encoding="utf-8")
    hooks = tmp_path / "installed-hooks"
    hooks.mkdir()
    shutil.copy(HOOKS / "session-ingest.sh", hooks / "session-start-memory.sh")
    shutil.copy(HOOKS / "render_brief.py", hooks / "render_brief.py")
    (hooks / "memorybrain-home").write_text(install.as_posix() + "\n", encoding="utf-8")
    project = tmp_path / "acme"
    project.mkdir()
    (project / ".brainproject").write_text("acme\n", encoding="utf-8")
    env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
           "HOME": str(tmp_path / "home"), "STUB_STATUS": str(status)}
    env.update(extra_env or {})
    r = subprocess.run(["bash", str(hooks / "session-start-memory.sh"), str(project)],
                       env=env, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    marker = install / "cli" / "scan-ran"
    for _ in range(40):                     # the scan runs in the background
        if marker.exists():
            break
        time.sleep(0.1)
    return marker


@needs_bash
def test_a_stale_file_index_starts_a_scan_in_the_background(tmp_path):
    marker = _run_session_hook(tmp_path, {"roots": 1, "scan_age_hours": 30.5})
    assert marker.exists() and marker.read_text() == "scan"


@needs_bash
@pytest.mark.parametrize("workspace", [{"roots": 1, "scan_age_hours": 2.0},
                                       {"roots": 0, "scan_age_hours": None}, {}])
def test_a_fresh_index_or_no_roots_starts_nothing(tmp_path, workspace):
    assert not _run_session_hook(tmp_path, workspace).exists()


@needs_bash
def test_auto_scan_can_be_turned_off(tmp_path):
    marker = _run_session_hook(tmp_path, {"roots": 1, "scan_age_hours": 300},
                               extra_env={"MEMORYBRAIN_AUTO_SCAN": "off"})
    assert not marker.exists()


# -------------------------------------------------------------- /status

def test_status_reports_how_old_the_file_index_is(tmp_db, monkeypatch):
    from app.db import connect
    from app.main import app
    monkeypatch.setattr("app.main.DB_PATH", tmp_db)
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    client = TestClient(app)
    assert client.get("/status").json()["workspace"] == {
        "roots": 0, "last_scan": "", "scan_age_hours": None}
    ws.upsert_root("git", "PC", "C:\\work", tmp_db)
    old = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
    conn = connect(tmp_db)
    conn.execute("UPDATE workspace_roots SET last_seen = ?", (old,))
    conn.commit()
    conn.close()
    got = client.get("/status").json()["workspace"]
    assert got["roots"] == 1 and got["last_scan"] == old and 49.9 <= got["scan_age_hours"] <= 50.1


# ------------------------------------------------------ capture at session end

def _transcript(path: Path, turns: int = 6) -> Path:
    events = []
    for n in range(turns):
        events.append({"type": "user", "message": {"role": "user",
                                                   "content": f"please fix thing {n}"}})
        events.append({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"Fixed thing {n} in the export job."}]}})
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _capture(tmp_path, hook_input: dict):
    sent = []

    def urlopen(req, timeout=0):
        sent.append(json.loads(req.data.decode("utf-8")))
        reply = MagicMock()
        reply.read.return_value = json.dumps({"id": "m1", "duplicate": False}).encode()
        reply.__enter__.return_value = reply
        return reply

    import io
    with patch.object(capture.urllib.request, "urlopen", urlopen):
        code = capture.main(io.StringIO(json.dumps(hook_input)))
    return code, sent


def test_session_end_posts_the_tail_with_its_own_source_and_a_quiet_window(tmp_path):
    (tmp_path / ".brainproject").write_text("acme\n", encoding="utf-8")
    code, sent = _capture(tmp_path, {
        "hook_event_name": "SessionEnd", "reason": "prompt_input_exit", "cwd": str(tmp_path),
        "transcript_path": str(_transcript(tmp_path / "t.jsonl"))})
    assert code == 0 and len(sent) == 1
    body = sent[0]
    assert body["project"] == "acme" and body["source"] == "session-end:prompt_input_exit"
    assert body["quiet_minutes"] == capture.SESSION_END_QUIET_MINUTES
    assert body["summary"].startswith("Auto-captured at session end, no session log was written")
    assert "please fix thing 5" in body["summary"]
    assert body["content"].startswith("Session transcript tail, captured automatically at "
                                      "session end (prompt_input_exit).")


def test_session_end_skips_a_session_too_short_to_matter(tmp_path):
    code, sent = _capture(tmp_path, {
        "hook_event_name": "SessionEnd", "reason": "other", "cwd": str(tmp_path),
        "transcript_path": str(_transcript(tmp_path / "t.jsonl", turns=1))})
    assert code == 0 and sent == []


def test_compaction_still_posts_with_no_quiet_window(tmp_path):
    code, sent = _capture(tmp_path, {
        "trigger": "auto", "cwd": str(tmp_path),
        "transcript_path": str(_transcript(tmp_path / "t.jsonl", turns=1))})
    assert code == 0 and len(sent) == 1
    assert sent[0]["source"] == "pre-compact:auto" and "quiet_minutes" not in sent[0]
    assert sent[0]["summary"].startswith("Auto-captured at compaction")


# ------------------------------------------------- the brain's side of it

@pytest.fixture
def api(tmp_db, fake_provider, monkeypatch):
    from app.main import app
    monkeypatch.setattr("app.ingest_pipeline.DB_PATH", tmp_db)
    monkeypatch.setattr("app.ingestion.session.DB_PATH", tmp_db, raising=False)
    monkeypatch.delenv("BRAIN_API_KEY", raising=False)
    return TestClient(app), tmp_db


def test_a_capture_is_skipped_when_a_session_log_was_just_written(api):
    client, db = api
    log = MemoryEntry(content="Session log written by the agent", type="session", project="acme",
                      timestamp=datetime.now(timezone.utc) - timedelta(minutes=20))
    add_memory(log, db_path=db)
    r = client.post("/ingest/session", json={
        "content": "Session transcript tail ...", "project": "acme",
        "source": "session-end:other", "quiet_minutes": 180})
    assert r.status_code == 200
    assert r.json() == {"skipped": "a session log was written 20 minutes ago",
                        "recent_id": log.id}


def test_a_capture_is_stored_when_the_last_log_is_older_than_the_window(api):
    client, db = api
    add_memory(MemoryEntry(content="Yesterday's log", type="session", project="acme",
                           timestamp=datetime.now(timezone.utc) - timedelta(hours=20)),
               db_path=db)
    r = client.post("/ingest/session", json={
        "content": "Session transcript tail: the export is fixed.", "project": "acme",
        "source": "session-end:other", "quiet_minutes": 180,
        "summary": "Auto-captured at session end, no session log was written: fix the export"})
    assert r.status_code == 201
    got = r.json()
    assert got["writer"] == "hook" and got["summary_source"] == "writer"
    assert got["summary"].startswith("Auto-captured at session end")
    assert "auto-capture" in got["tags"]


def test_another_projects_log_does_not_silence_this_one(api):
    client, db = api
    add_memory(MemoryEntry(content="Log in another project", type="session", project="other"),
               db_path=db)
    r = client.post("/ingest/session", json={
        "content": "Session transcript tail: acme work.", "project": "acme",
        "source": "session-end:other", "quiet_minutes": 180})
    assert r.status_code == 201
