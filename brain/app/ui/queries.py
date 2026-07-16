# brain/app/ui/queries.py
"""Read-only SQL for the web UI. Matches the real v2.0.0 schema:
memories(id, content, summary, type, project, tags JSON, source, importance,
         timestamp, status, superseded_by, supersedes, link_degree, linked_at)
projects(slug, name, last_activity, one_liner)
memories_fts(content, summary, tags)  -- FTS5, contentless-sync via triggers
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..storage import DB_PATH

VALID_TYPES = ("session", "handover", "note", "fact", "file", "reference")
VALID_SORTS = {
    "recent": "timestamp DESC",
    "importance": "importance DESC, timestamp DESC",
    "degree": "link_degree DESC, timestamp DESC",
}


def get_conn(db_path: Path = None) -> sqlite3.Connection:
    # check_same_thread=False: async routes may touch the connection from the
    # event-loop thread while the dependency created it in the threadpool.
    # Safe here — the connection is read-only (PRAGMA query_only) and unshared.
    conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")   # UI must never write
    return conn


def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def parse_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# ---------------------------------------------------------------- dashboard

def stats(conn) -> dict[str, Any]:
    return {
        "total": conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0],
        "by_type": _rows(conn, """
            SELECT type, COUNT(*) AS n FROM memories
            WHERE status = 'active' GROUP BY type ORDER BY n DESC"""),
        "projects": conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0],
        "edges": conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0],
    }


def recent_memories(conn, limit: int = 12,
                    project: Optional[str] = None) -> list[dict[str, Any]]:
    sql = """SELECT id, summary, type, project, importance, timestamp
             FROM memories WHERE status = 'active'"""
    params: list[Any] = []
    if project:
        sql += " AND project = ?"
        params.append(project)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    return _rows(conn, sql, tuple(params))


def top_by_degree(conn, limit: int = 8) -> list[dict[str, Any]]:
    return _rows(conn, """
        SELECT id, summary, type, project, importance, timestamp, link_degree
        FROM memories WHERE status = 'active' AND link_degree > 0
        ORDER BY link_degree DESC, timestamp DESC LIMIT ?""", (limit,))


def all_projects(conn) -> list[dict[str, Any]]:
    return _rows(conn, """
        SELECT p.slug, p.name, p.last_activity, p.one_liner,
               (SELECT COUNT(*) FROM memories m
                 WHERE m.project = p.slug AND m.status = 'active') AS memory_count
        FROM projects p ORDER BY p.last_activity DESC""")


# ------------------------------------------------------------------ project

def project_memories(conn, slug: str, mtype: Optional[str] = None,
                     min_importance: int = 1, sort: str = "recent",
                     limit: int = 100) -> list[dict[str, Any]]:
    order = VALID_SORTS.get(sort, VALID_SORTS["recent"])
    sql = """SELECT id, summary, type, project, tags, importance, timestamp,
                    COALESCE(link_degree, 0) AS link_degree
             FROM memories
             WHERE status = 'active' AND project = ? AND importance >= ?"""
    params: list[Any] = [slug, min_importance]
    if mtype in VALID_TYPES:
        sql += " AND type = ?"
        params.append(mtype)
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)
    out = _rows(conn, sql, tuple(params))
    for m in out:
        m["tags"] = parse_tags(m["tags"])
    return out


# ------------------------------------------------------------------- memory

def get_memory_row(conn, memory_id: str) -> Optional[dict[str, Any]]:
    rows = _rows(conn, "SELECT * FROM memories WHERE id = ?", (memory_id,))
    if not rows:
        return None
    m = rows[0]
    m["tags"] = parse_tags(m["tags"])
    return m


# ------------------------------------------------------------------- search

def search_fts(conn, q: str, project: Optional[str] = None,
               mtype: Optional[str] = None, limit: int = 30) -> list[dict[str, Any]]:
    """Keyword-only fallback used when the AI provider (embeddings) is down."""
    fts_q = " ".join(f'"{t}"' for t in q.replace('"', "").split())
    if not fts_q:
        return []
    sql = """SELECT m.id, m.summary, m.type, m.project, m.importance, m.timestamp,
                    bm25(memories_fts) AS score
             FROM memories_fts f JOIN memories m ON m.rowid = f.rowid
             WHERE memories_fts MATCH ? AND m.status = 'active'"""
    params: list[Any] = [fts_q]
    if project:
        sql += " AND m.project = ?"
        params.append(project)
    if mtype in VALID_TYPES:
        sql += " AND m.type = ?"
        params.append(mtype)
    sql += " ORDER BY score LIMIT ?"
    params.append(limit)
    try:
        return _rows(conn, sql, tuple(params))
    except sqlite3.OperationalError:
        return []
