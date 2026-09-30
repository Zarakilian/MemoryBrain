"""Project pins — the multi-AI working set."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect, get_memory

PIN_KINDS = frozenset(
    {"goal", "truth", "branch", "constraint", "open_loop", "custom"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_pins(project: str, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT p.project, p.memory_id, p.kind, p.label, p.priority,
                      p.created_at, m.summary, m.type, m.status, m.importance,
                      substr(m.content, 1, 240) AS content_preview
               FROM project_pins p
               JOIN memories m ON m.id = p.memory_id
               WHERE p.project = ?
               ORDER BY p.priority DESC, p.created_at ASC""",
            (project,),
        ).fetchall()
    return [dict(r) for r in rows]


def pinned_ids(project: Optional[str] = None,
               db_path: Path = DB_PATH) -> set[str]:
    with _connect(db_path) as conn:
        if project:
            rows = conn.execute(
                "SELECT memory_id FROM project_pins WHERE project = ?",
                (project,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT memory_id FROM project_pins").fetchall()
    return {r["memory_id"] for r in rows}


def pin_memory(
    project: str,
    memory_id: str,
    kind: str = "truth",
    label: str = "",
    priority: int = 0,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    kind = (kind or "truth").strip().lower()
    if kind not in PIN_KINDS:
        return {"error": f"kind must be one of: {', '.join(sorted(PIN_KINDS))}"}
    entry = get_memory(memory_id, db_path=db_path)
    if entry is None:
        return {"error": f"Memory {memory_id} not found"}
    if entry.status != "active":
        return {"error": "Only active memories can be pinned"}
    if entry.project != project:
        return {
            "error": (
                f"Memory belongs to project '{entry.project}', "
                f"not '{project}'"
            )
        }
    priority = max(-100, min(int(priority), 100))
    label = (label or "")[:200]
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO project_pins
               (project, memory_id, kind, label, priority, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(project, memory_id) DO UPDATE SET
                   kind=excluded.kind,
                   label=excluded.label,
                   priority=excluded.priority""",
            (project, memory_id, kind, label, priority, _now()),
        )
        conn.commit()
    return {
        "pinned": True,
        "project": project,
        "memory_id": memory_id,
        "kind": kind,
        "label": label,
        "priority": priority,
    }


def unpin_memory(project: str, memory_id: str,
                 db_path: Path = DB_PATH) -> dict[str, Any]:
    with _connect(db_path) as conn:
        cur = conn.execute(
            "DELETE FROM project_pins WHERE project = ? AND memory_id = ?",
            (project, memory_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            return {"error": "Pin not found"}
    return {"unpinned": True, "project": project, "memory_id": memory_id}
