"""Token-budgeted project briefing packs for multi-AI session start."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .conflicts import list_conflicts
from .pins import list_pins
from .policy import get_policy
from .storage import DB_PATH, _connect, get_next_session_notes, get_project

DEFAULT_BRIEF_CHARS = 3500
SYSTEM_PROJECT = "system"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _preview(text: str, n: int = 220) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1].rstrip() + "…"


def _policy(project: str, db_path: Path) -> dict[str, Any]:
    p = get_policy(project, db_path=db_path)
    return {
        "include_system": 1 if p.get("include_system") else 0,
        "max_brief_chars": p.get("max_brief_chars") or DEFAULT_BRIEF_CHARS,
        "default_tags": p.get("default_tags") or [],
        "notes": p.get("notes") or "",
    }


def _memories_by_types(
    project: str,
    types: tuple[str, ...],
    limit: int,
    db_path: Path,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(types))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT id, summary, type, importance, timestamp, tags,
                       substr(content, 1, 280) AS content_preview,
                       COALESCE(strength, 1.0) AS strength
                FROM memories
                WHERE project = ? AND status = 'active'
                  AND type IN ({placeholders})
                ORDER BY importance DESC, strength DESC, timestamp DESC
                LIMIT ?""",
            (project, *types, limit),
        ).fetchall()
    out = []
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except (ValueError, TypeError):
            tags = []
        out.append({
            "id": r["id"],
            "type": r["type"],
            "summary": r["summary"] or _preview(r["content_preview"]),
            "importance": r["importance"],
            "strength": r["strength"],
            "timestamp": r["timestamp"],
            "tags": tags,
            "content_preview": r["content_preview"],
        })
    return out


def _belief_sources(belief_id: str, db_path: Path,
                    limit: int = 6) -> list[dict[str, str]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT m.id, m.summary, substr(m.content, 1, 160) AS preview
               FROM memory_links l
               JOIN memories m ON m.id = l.dst_id
               WHERE l.src_id = ? AND l.kind = 'derived_from'
               LIMIT ?""",
            (belief_id, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "summary": r["summary"] or r["preview"] or "",
        }
        for r in rows
    ]


def _open_loops(project: str, limit: int, db_path: Path) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, summary, type, timestamp, tags,
                      substr(content, 1, 280) AS content_preview
               FROM memories
               WHERE project = ? AND status = 'active'
                 AND (
                   type = 'open_loop'
                   OR tags LIKE '%open_loop%'
                   OR tags LIKE '%next_session%'
                 )
               ORDER BY timestamp DESC
               LIMIT ?""",
            (project, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "summary": r["summary"] or _preview(r["content_preview"]),
            "timestamp": r["timestamp"],
            "content_preview": r["content_preview"],
        }
        for r in rows
    ]


def _recent(project: str, days: int, limit: int,
            db_path: Path) -> list[dict[str, Any]]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, summary, type, timestamp,
                      substr(content, 1, 200) AS content_preview
               FROM memories
               WHERE project = ? AND status = 'active' AND timestamp >= ?
               ORDER BY timestamp DESC
               LIMIT ?""",
            (project, cutoff, limit),
        ).fetchall()
    return [
        {
            "id": r["id"],
            "type": r["type"],
            "summary": r["summary"] or _preview(r["content_preview"]),
            "timestamp": r["timestamp"],
        }
        for r in rows
    ]


def _system_ops(limit: int, db_path: Path) -> list[dict[str, Any]]:
    return _memories_by_types(
        SYSTEM_PROJECT, ("fact", "decision", "note"), limit, db_path
    )


def _trim_to_budget(pack: dict[str, Any], budget: int) -> dict[str, Any]:
    """Drop lower-priority sections until JSON fits roughly in budget chars."""
    # Priority order for truncation (last dropped first)
    drop_order = [
        "intent_hits",
        "recent",
        "system_ops",
        "open_loops",
        "facts_and_decisions",
        "beliefs",
        "conflicts",
        # pins and next_session and meta kept longest
    ]
    packed = json.dumps(pack, default=str)
    if len(packed) <= budget:
        pack["chars_used"] = len(packed)
        pack["truncated"] = False
        return pack

    for key in drop_order:
        if key not in pack:
            continue
        if isinstance(pack[key], list) and pack[key]:
            # shrink list gradually
            while pack[key] and len(json.dumps(pack, default=str)) > budget:
                pack[key].pop()
        packed = json.dumps(pack, default=str)
        if len(packed) <= budget:
            pack["chars_used"] = len(packed)
            pack["truncated"] = True
            return pack

    # Last resort: hard-cut long strings in remaining lists
    pack["chars_used"] = len(json.dumps(pack, default=str))
    pack["truncated"] = True
    return pack


async def build_project_brief(
    project: str,
    intent: Optional[str] = None,
    max_chars: Optional[int] = None,
    include_system: Optional[bool] = None,
    days: int = 14,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Assemble a fixed-budget context pack for one project."""
    warnings: list[str] = []
    if not project or not project.strip():
        return {"error": "project is required"}

    project = project.strip()
    policy = _policy(project, db_path)
    budget = max_chars or policy["max_brief_chars"] or DEFAULT_BRIEF_CHARS
    budget = max(800, min(int(budget), 12_000))

    proj_row = get_project(project, db_path=db_path)
    if proj_row is None:
        warnings.append(f"Project '{project}' has no project row yet (empty or new).")

    pins = list_pins(project, db_path=db_path)
    pin_payload = [
        {
            "memory_id": p["memory_id"],
            "kind": p["kind"],
            "label": p["label"],
            "priority": p["priority"],
            "type": p.get("type"),
            "summary": p.get("summary") or _preview(p.get("content_preview") or ""),
            "status": p.get("status"),
        }
        for p in pins
        if p.get("status") == "active"
    ]

    beliefs_raw = _memories_by_types(project, ("belief",), 8, db_path)
    beliefs = []
    for b in beliefs_raw:
        item = dict(b)
        item["sources"] = _belief_sources(b["id"], db_path)
        beliefs.append(item)

    facts = _memories_by_types(project, ("fact", "decision"), 12, db_path)
    loops = _open_loops(project, 8, db_path)
    conflicts = list_conflicts(project=project, limit=10, db_path=db_path)
    recent = _recent(project, days=days, limit=8, db_path=db_path)
    next_notes = get_next_session_notes(project, db_path=db_path)

    use_system = policy["include_system"] if include_system is None else include_system
    system_ops = _system_ops(5, db_path) if use_system and project != SYSTEM_PROJECT else []

    intent_hits: list[dict[str, Any]] = []
    if intent and intent.strip():
        try:
            from .search import hybrid_search
            hits = await hybrid_search(
                intent.strip(), limit=5, project=project, db_path=db_path
            )
            intent_hits = [
                {
                    "id": h.get("id"),
                    "summary": h.get("summary") or h.get("content_preview", ""),
                    "score": h.get("score"),
                    "type": h.get("type"),
                }
                for h in hits
            ]
        except Exception as e:
            warnings.append(f"intent search unavailable: {type(e).__name__}")

    pack: dict[str, Any] = {
        "project": project,
        "project_name": proj_row.name if proj_row else project,
        "generated_at": _now(),
        "char_budget": budget,
        "days": days,
        "pins": pin_payload,
        "beliefs": beliefs,
        "facts_and_decisions": facts,
        "open_loops": loops,
        "conflicts": conflicts.get("pairs", []),
        "conflict_count": conflicts.get("total", 0),
        "recent": recent,
        "next_session": next_notes or "",
        "system_ops": system_ops,
        "intent": intent or "",
        "intent_hits": intent_hits,
        "policy_notes": policy.get("notes") or "",
        "warnings": warnings,
        "protocol_hint": (
            "Prefer pins + beliefs + facts/decisions as current truth. "
            "Resolve conflicts before writing new facts on the same topic. "
            "Write durable truths as type=fact or type=decision; "
            "unfinished work as type=open_loop; narrative as type=session."
        ),
    }
    return _trim_to_budget(pack, budget)
