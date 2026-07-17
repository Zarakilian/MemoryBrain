# brain/app/graph_queries.py
"""Read-only graph queries — shared by the MCP tools and the web UI."""
import json
import sqlite3
from pathlib import Path
from typing import Optional

from .storage import DB_PATH
from .linker import combined_weights

VALID_KINDS = {"semantic", "tag", "reference", "session_chain", "entity"}


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_related(memory_id: str, limit: int = 10, min_weight: float = 0.3,
                kinds: Optional[list[str]] = None, include_archived: bool = False,
                db_path: Path = None) -> Optional[dict]:
    """Neighbours of a memory ranked by noisy-OR combined weight, with
    per-kind explanations. Summaries only (same token philosophy as search)."""
    db_path = db_path or DB_PATH
    kinds = [k for k in (kinds or []) if k in VALID_KINDS] or None

    with _conn(db_path) as conn:
        if not conn.execute("SELECT 1 FROM memories WHERE id = ?", (memory_id,)).fetchone():
            return None
        # outgoing + symmetric edges
        rows = conn.execute(
            """SELECT dst_id, kind, weight, meta, 'out' AS direction
               FROM memory_links_all WHERE src_id = ?
               UNION ALL
               SELECT src_id, kind, weight, meta, 'in' AS direction
               FROM memory_links WHERE dst_id = ? AND directed = 1""",
            (memory_id, memory_id)).fetchall()

        per_dst: dict[str, list[dict]] = {}
        for r in rows:
            if kinds and r["kind"] not in kinds:
                continue
            per_dst.setdefault(r["dst_id"], []).append({
                "kind": r["kind"], "weight": r["weight"],
                "direction": r["direction"],
                "meta": json.loads(r["meta"] or "{}"),
            })

        combined = []
        for dst, expl in per_dst.items():
            acc = 1.0
            for e in expl:
                acc *= (1.0 - min(e["weight"], 0.999))
            w = 1.0 - acc
            if w < min_weight:
                continue
            m = conn.execute(
                """SELECT summary, type, project, importance, timestamp, status
                   FROM memories WHERE id = ?""", (dst,)).fetchone()
            if not m or (not include_archived and m["status"] != "active"):
                continue
            combined.append({
                "id": dst, "summary": m["summary"], "type": m["type"],
                "project": m["project"], "importance": m["importance"],
                "timestamp": m["timestamp"],
                "w_combined": round(w, 4),
                "kinds": sorted({e["kind"] for e in expl}),
                "explanations": expl,
            })

    combined.sort(key=lambda x: (-x["w_combined"], x["timestamp"]), reverse=False)
    combined.sort(key=lambda x: -x["w_combined"])
    return {"memory_id": memory_id, "related": combined[:limit]}


def get_graph(project: Optional[str] = None, min_weight: float = 0.35,
              max_nodes: int = 150, include_archived: bool = False,
              db_path: Path = None) -> dict:
    """Nodes + combined-weight edges for the graph view. Node selection when
    over max_nodes: highest weighted degree first, then recency."""
    db_path = db_path or DB_PATH
    max_nodes = max(1, min(max_nodes, 500))

    with _conn(db_path) as conn:
        sql = """SELECT id, substr(COALESCE(NULLIF(summary,''), content), 1, 80) AS label,
                        type, project, importance, timestamp,
                        COALESCE(link_degree, 0) AS degree
                 FROM memories"""
        where, params = [], []
        if not include_archived:
            where.append("status = 'active'")
        if project:
            where.append("project = ?")
            params.append(project)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY link_degree DESC, timestamp DESC LIMIT ?"
        params.append(max_nodes + 1)
        nodes = [dict(r) for r in conn.execute(sql, params).fetchall()]
        truncated = len(nodes) > max_nodes
        nodes = nodes[:max_nodes]
        ids = {n["id"] for n in nodes}

        raw = conn.execute(
            "SELECT src_id, dst_id, kind, weight FROM memory_links").fetchall()

    pair_acc: dict[tuple, dict] = {}
    for r in raw:
        if r["src_id"] not in ids or r["dst_id"] not in ids:
            continue
        key = tuple(sorted((r["src_id"], r["dst_id"])))
        p = pair_acc.setdefault(key, {"inv": 1.0, "kinds": set()})
        p["inv"] *= (1.0 - min(r["weight"], 0.999))
        p["kinds"].add(r["kind"])

    edges = []
    for (a, b), p in pair_acc.items():
        w = 1.0 - p["inv"]
        if w >= min_weight:
            edges.append({"src": a, "dst": b, "w": round(w, 4),
                          "kinds": sorted(p["kinds"])})

    return {"nodes": nodes, "edges": edges, "truncated": truncated}
