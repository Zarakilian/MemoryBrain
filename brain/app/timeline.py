"""Project timeline and entity cards for multi-AI orientation."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect

# Crude but useful entity extraction: CamelCase tokens, #tags, path-like tokens
_CAMEL = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b")
_HASH_TAG = re.compile(r"(?<!\w)#([a-zA-Z][\w-]{1,40})")
_PATHISH = re.compile(r"(?:[A-Za-z]:\\|/)(?:[^\s\"']+){6,}")
_SERVICE = re.compile(
    r"\b(?:localhost:\d{2,5}|memorybrain|ollama|docker|github|vercel)\b",
    re.I,
)


def get_timeline(
    project: Optional[str] = None,
    days: int = 30,
    limit: int = 100,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Chronological feed of notable memories (sessions, decisions, facts, beliefs)."""
    days = max(1, min(int(days), 365))
    limit = max(1, min(int(limit), 500))
    sql = """SELECT id, summary, type, project, importance, timestamp,
                    substr(content, 1, 240) AS content_preview, tags
             FROM memories
             WHERE status = 'active'
               AND type IN ('session','handover','decision','fact','belief',
                            'open_loop','note')
               AND timestamp >= datetime('now', ?)"""
    params: list[Any] = [f"-{days} days"]
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
    """Entity cards: tags + lightweight extracted names from recent content.

    Also surfaces graph `entity` edges when present.
    """
    limit = max(1, min(int(limit), 100))
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
        for m in _CAMEL.findall(text):
            if len(m) >= 4:
                bump(m, "name", mid, summary)
        for m in _SERVICE.findall(text):
            bump(m, "service", mid, summary)

    for e in edges:
        try:
            meta = json.loads(e["meta"] or "{}")
        except (ValueError, TypeError):
            meta = {}
        label = meta.get("entity") or meta.get("name") or e["dst_id"][:12]
        bump(str(label), "graph", e["src_id"], "")

    ranked = sorted(counts.values(), key=lambda x: (-x["mentions"], x["name"]))
    return {
        "project": project or "",
        "count": min(len(ranked), limit),
        "entities": ranked[:limit],
    }
