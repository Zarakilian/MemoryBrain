"""Shared conflict list / resolve / dismiss logic for Atlas UI and MCP."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect, archive_memory, get_memory
from .vector import vec_update_metadata

logger = logging.getLogger(__name__)


def _edge_key(a: str, b: str) -> tuple[str, str]:
    return (min(a, b), max(a, b))


def conflict_edge_exists(a: str, b: str, db_path: Path = DB_PATH) -> bool:
    lo, hi = _edge_key(a, b)
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT 1 FROM memory_links
               WHERE kind = 'conflicts_with' AND src_id = ? AND dst_id = ?
                 AND weight > 0.05""",
            (lo, hi),
        ).fetchone()
    return row is not None


def list_conflicts(project: Optional[str] = None, limit: int = 50,
                   db_path: Path = DB_PATH) -> dict[str, Any]:
    """Active contradiction pairs (both sides active, not dismissed)."""
    sql = """SELECT l.src_id, l.dst_id, l.weight, l.meta,
                    a.summary AS src_summary, a.type AS src_type,
                    a.project AS src_project, a.timestamp AS src_timestamp,
                    a.content AS src_content,
                    b.summary AS dst_summary, b.type AS dst_type,
                    b.timestamp AS dst_timestamp, b.content AS dst_content
             FROM memory_links l
             JOIN memories a ON a.id = l.src_id AND a.status = 'active'
             JOIN memories b ON b.id = l.dst_id AND b.status = 'active'
             WHERE l.kind = 'conflicts_with'
               AND l.weight > 0.05"""
    params: list[Any] = []
    if project:
        sql += " AND a.project = ?"
        params.append(project)
    sql += " ORDER BY l.weight DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))

    pairs = []
    with _connect(db_path) as conn:
        for r in conn.execute(sql, params).fetchall():
            try:
                meta = json.loads(r["meta"] or "{}")
            except (ValueError, TypeError):
                meta = {}
            pairs.append({
                "a": {
                    "id": r["src_id"],
                    "summary": r["src_summary"] or (r["src_content"] or "")[:160],
                    "type": r["src_type"],
                    "timestamp": r["src_timestamp"],
                },
                "b": {
                    "id": r["dst_id"],
                    "summary": r["dst_summary"] or (r["dst_content"] or "")[:160],
                    "type": r["dst_type"],
                    "timestamp": r["dst_timestamp"],
                },
                "project": r["src_project"],
                "similarity": r["weight"],
                "flagged_at": meta.get("flagged_at"),
            })
    return {"pairs": pairs, "total": len(pairs)}


def dismiss_conflict(a_id: str, b_id: str,
                     db_path: Path = DB_PATH) -> dict[str, Any]:
    """Keep both: tombstone the conflicts_with edge so it will not re-flag
    as a live conflict (weight sunk + meta.dismissed)."""
    if a_id == b_id:
        return {"error": "a_id and b_id must differ"}
    lo, hi = _edge_key(a_id, b_id)
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT weight FROM memory_links
               WHERE kind = 'conflicts_with' AND src_id = ? AND dst_id = ?""",
            (lo, hi),
        ).fetchone()
        if row is None:
            return {"error": "No such contradiction"}
        conn.execute(
            """UPDATE memory_links SET weight = 0.01, meta = ?
               WHERE kind = 'conflicts_with' AND src_id = ? AND dst_id = ?""",
            (json.dumps({"dismissed": True}), lo, hi),
        )
        conn.commit()
    return {"dismissed": [lo, hi]}


def resolve_conflict(winner_id: str, loser_id: str,
                     db_path: Path = DB_PATH) -> dict[str, Any]:
    """Winner stays active; loser is archived (reversible supersession)."""
    if winner_id == loser_id:
        return {"error": "winner and loser must differ"}
    winner = get_memory(winner_id, db_path=db_path)
    loser = get_memory(loser_id, db_path=db_path)
    if winner is None or loser is None:
        return {"error": "Memory not found"}
    if not conflict_edge_exists(winner_id, loser_id, db_path=db_path):
        # Still allow resolve if both active and user is sure? Prefer strict.
        return {"error": "No such contradiction"}
    archive_memory(loser_id, superseded_by=winner_id, db_path=db_path)
    try:
        vec_update_metadata(loser_id, {"status": "archived"}, db_path=db_path)
    except TypeError:
        try:
            vec_update_metadata(loser_id, {"status": "archived"})
        except Exception:
            logger.warning("vector status update failed for %s", loser_id)
    except Exception:
        logger.warning("vector status update failed for %s", loser_id)
    return {"winner": winner_id, "archived": loser_id}
