"""Per-project brief policy — shared by brief builder, MCP, and Atlas UI."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect

DEFAULT_MAX_CHARS = 3500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_policy(project: str, db_path: Path = DB_PATH) -> dict[str, Any]:
    with _connect(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT * FROM project_policy WHERE project = ?", (project,)
            ).fetchone()
        except Exception:
            row = None
    if row is None:
        return {
            "project": project,
            "include_system": True,
            "max_brief_chars": DEFAULT_MAX_CHARS,
            "default_tags": [],
            "notes": "",
            "exists": False,
        }
    try:
        tags = json.loads(row["default_tags"] or "[]")
    except (ValueError, TypeError):
        tags = []
    return {
        "project": project,
        "include_system": bool(int(row["include_system"])),
        "max_brief_chars": int(row["max_brief_chars"] or DEFAULT_MAX_CHARS),
        "default_tags": tags if isinstance(tags, list) else [],
        "notes": row["notes"] or "",
        "updated_at": row["updated_at"],
        "exists": True,
    }


def set_policy(
    project: str,
    include_system: Optional[bool] = None,
    max_brief_chars: Optional[int] = None,
    default_tags: Optional[list] = None,
    notes: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    if not project or not project.strip():
        return {"error": "project is required"}
    project = project.strip()
    cur = get_policy(project, db_path=db_path)
    inc = cur["include_system"] if include_system is None else bool(include_system)
    chars = cur["max_brief_chars"] if max_brief_chars is None else int(max_brief_chars)
    chars = max(800, min(chars, 12000))
    tags = cur["default_tags"] if default_tags is None else list(default_tags)[:20]
    notes_s = cur["notes"] if notes is None else str(notes)[:2000]
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO project_policy
               (project, include_system, max_brief_chars, default_tags, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(project) DO UPDATE SET
                   include_system=excluded.include_system,
                   max_brief_chars=excluded.max_brief_chars,
                   default_tags=excluded.default_tags,
                   notes=excluded.notes,
                   updated_at=excluded.updated_at""",
            (project, 1 if inc else 0, chars, json.dumps(tags), notes_s, _now()),
        )
        conn.commit()
    return get_policy(project, db_path=db_path)
