"""Learning from corrections: how the user wants things done.

An agent that is corrected records the rule (record_correction). It waits as
a proposed procedure until the user confirms it in Atlas or with
`brain procedures --confirm`; no MCP tool can confirm one. Only confirmed
rules (trust "user") reach the brief, under "How you want things done".
Project "system" holds rules that apply everywhere.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import storage as _storage
from .models import MemoryEntry, PROJECT_SLUG_RE

SYSTEM_PROJECT = "system"
MAX_RULE_CHARS = 500
MAX_EVIDENCE_CHARS = 1000

_FIELDS = "id, project, summary, content, status, trust, writer, timestamp"


def _rows(sql: str, params: tuple, db_path) -> list[dict]:
    with _storage._connect(db_path or _storage.DB_PATH) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def record_correction(rule: str, evidence: str = "", project: Optional[str] = None,
                      writer: str = "", db_path: Optional[Path] = None) -> dict:
    """Store a proposed procedure. The same rule (same words, same project) a
    second time is a duplicate, never a second row."""
    from .redact import redact
    path = db_path or _storage.DB_PATH
    # Redacted first: the rule is the summary every brief carries once confirmed.
    rule, _ = redact(" ".join((rule or "").split()))
    evidence, _ = redact(" ".join((evidence or "").split()))
    project = project or SYSTEM_PROJECT
    if not rule:
        raise ValueError("rule must not be empty")
    if len(rule) > MAX_RULE_CHARS:
        raise ValueError(f"rule exceeds {MAX_RULE_CHARS} characters")
    if len(evidence) > MAX_EVIDENCE_CHARS:
        raise ValueError(f"evidence exceeds {MAX_EVIDENCE_CHARS} characters")
    if not PROJECT_SLUG_RE.match(project):
        raise ValueError("project must match ^[a-z0-9_-]{1,64}$")
    existing = _rows(f"""SELECT {_FIELDS} FROM memories WHERE type = 'procedure' AND project = ?
                         AND summary = ? AND status IN ('proposed', 'active')""",
                     (project, rule), path)
    if existing:
        return {**existing[0], "duplicate": True}
    # the user already said no to this exact rule: an agent may not queue it again
    turned_down = _rows(f"""SELECT {_FIELDS} FROM memories m WHERE type = 'procedure'
                            AND project = ? AND summary = ? AND status = 'archived'
                            AND EXISTS (SELECT 1 FROM memory_audit a
                                        WHERE a.memory_id = m.id AND a.action = 'reject')""",
                        (project, rule), path)
    if turned_down:
        return {**turned_down[0], "duplicate": True, "rejected": True}
    content = rule + (f"\n\nWhat the user said: {evidence}" if evidence else "")
    entry = MemoryEntry(content=content, summary=rule, type="procedure", project=project,
                        tags=["procedure"], source="record_correction", importance=4,
                        status="proposed", trust="agent", writer=writer or "mcp",
                        embedded=False)  # the background re-embed indexes it
    _storage.add_memory(entry, db_path=path)
    _storage.upsert_project(_storage.Project(slug=project, name=project.replace("-", " ").title()),
                            db_path=path)
    return {"id": entry.id, "project": project, "summary": rule, "status": "proposed",
            "duplicate": False}


def _decide(memory_id: str, status: str, action: str, actor: str, db_path) -> bool:
    with _storage._connect(db_path or _storage.DB_PATH) as conn:
        sets = "status = ?, trust = 'user'" if status == "active" else "status = ?"
        cur = conn.execute(
            f"UPDATE memories SET {sets} WHERE id = ? AND type = 'procedure' AND status = 'proposed'",
            (status, memory_id))
        if not cur.rowcount:
            return False
        _storage._audit(conn, memory_id, action, actor)
        conn.commit()
    return True


def confirm_procedure(memory_id: str, actor: str, db_path: Optional[Path] = None) -> bool:
    """The user makes a proposed rule official: active, trust "user", audited."""
    return _decide(memory_id, "active", "confirm", actor, db_path)


def reject_procedure(memory_id: str, actor: str, db_path: Optional[Path] = None) -> bool:
    """The user turns a proposed rule down: archived (reversible), audited."""
    return _decide(memory_id, "archived", "reject", actor, db_path)


def active_procedures(project: str, db_path: Optional[Path] = None) -> list[dict]:
    """Confirmed rules for this project plus the ones that apply everywhere."""
    return _rows(f"""SELECT {_FIELDS} FROM memories
                     WHERE type = 'procedure' AND status = 'active' AND trust = 'user'
                       AND project IN (?, ?) ORDER BY timestamp DESC""",
                 (project, SYSTEM_PROJECT), db_path)


def proposed_procedures(db_path: Optional[Path] = None) -> list[dict]:
    return _rows(f"""SELECT {_FIELDS} FROM memories
                     WHERE type = 'procedure' AND status = 'proposed' ORDER BY timestamp DESC""",
                 (), db_path)
