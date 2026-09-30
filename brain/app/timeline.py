"""Project timeline and entity cards for multi-AI orientation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect

# Named entities come from entities.py (indexed at write time); #hashtags stay
# a light extra for memories written before v3.
_HASH_TAG = re.compile(r"(?<!\w)#([a-zA-Z][\w-]{1,40})")


def get_timeline(
    project: Optional[str] = None,
    days: int = 30,
    limit: int = 100,
    db_path: Path = DB_PATH,
    as_of: Optional[str] = None,
) -> dict[str, Any]:
    """Chronological feed of notable memories (sessions, decisions, facts, beliefs).

    as_of (ISO date or datetime) shows the state at that moment: what had been
    written by then and was still valid, archived rows included."""
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 500))
    sql = """SELECT id, summary, type, project, importance, timestamp,
                    substr(content, 1, 240) AS content_preview, tags
             FROM memories
             WHERE type IN ('session','handover','decision','fact','belief',
                            'open_loop','note')
               AND timestamp >= datetime('now', ?)"""
    params: list[Any] = [f"-{days} days"]
    if as_of:
        from .search import _as_of_moment
        moment = _as_of_moment(as_of)
        if moment is None:
            raise ValueError("as_of must be an ISO date or datetime")
        at = moment.isoformat()
        sql += """ AND status IN ('active', 'archived', 'done') AND timestamp <= ?
                   AND (valid_from IS NULL OR valid_from <= ?)
                   AND (valid_to IS NULL OR valid_to > ?)"""
        params += [at, at, at]
    else:
        sql += " AND status = 'active'"
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    events = []
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except (ValueError, TypeError):
            tags = []
        events.append({
            "id": r["id"],
            "type": r["type"],
            "project": r["project"],
            "summary": r["summary"] or (r["content_preview"] or "")[:160],
            "importance": r["importance"],
            "timestamp": r["timestamp"],
            "tags": tags,
        })
    return {
        "project": project or "",
        "days": days,
        "count": len(events),
        "events": events,
    }


def get_entities(
    project: Optional[str] = None,
    limit: int = 40,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Entity cards: the hosts, tickets, ids, paths, products and env vars
    indexed at write time (entities.py), plus tags and #hashtags from recent
    memories (useful for memories written before v3). Each card carries its
    mention count and up to 3 example memory ids."""
    from .entities import top_entities

    limit = max(1, min(int(limit), 100))
    indexed = top_entities(project, limit=limit, db_path=db_path)
    with _connect(db_path) as conn:
        sql = """SELECT id, summary, content, tags, type, project, timestamp
                 FROM memories WHERE status = 'active'"""
        params: list[Any] = []
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY timestamp DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()

        # graph entity edges
        edge_sql = """SELECT l.src_id, l.dst_id, l.weight, l.meta
                      FROM memory_links l
                      WHERE l.kind = 'entity'"""
        if project:
            edge_sql = """SELECT l.src_id, l.dst_id, l.weight, l.meta
                          FROM memory_links l
                          JOIN memories a ON a.id = l.src_id
                          WHERE l.kind = 'entity' AND a.project = ?"""
            edges = conn.execute(edge_sql, (project,)).fetchall()
        else:
            edges = conn.execute(edge_sql).fetchall()

    counts: dict[str, dict[str, Any]] = {}

    def bump(name: str, kind: str, mid: str, summary: str):
        key = name.lower()
        if key not in counts:
            counts[key] = {
                "name": name,
                "kind": kind,
                "mentions": 0,
                "memory_ids": [],
                "examples": [],
            }
        c = counts[key]
        c["mentions"] += 1
        if mid not in c["memory_ids"] and len(c["memory_ids"]) < 8:
            c["memory_ids"].append(mid)
        if summary and len(c["examples"]) < 3:
            c["examples"].append(summary[:140])

    for r in rows:
        mid = r["id"]
        summary = r["summary"] or (r["content"] or "")[:120]
        try:
            tags = json.loads(r["tags"] or "[]")
        except (ValueError, TypeError):
            tags = []
        for t in tags:
            if isinstance(t, str) and t.strip():
                bump(t.strip(), "tag", mid, summary)
        text = f"{r['summary'] or ''}\n{r['content'] or ''}"
        for m in _HASH_TAG.findall(text):
            bump(m, "hashtag", mid, summary)

    for e in edges:
        try:
            meta = json.loads(e["meta"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        label = meta.get("entity") or meta.get("name") or e["dst_id"][:12]
        bump(str(label), "graph", e["src_id"], "")

    known = {e["name"].lower() for e in indexed}
    light = [{**c, "memory_ids": c["memory_ids"][:3]} for c in counts.values()
             if c["name"].lower() not in known]
    ranked = sorted(indexed + light, key=lambda x: (-x["mentions"], x["name"]))
    return {
        "project": project or "",
        "count": min(len(ranked), limit),
        "entities": ranked[:limit],
    }
