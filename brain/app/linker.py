# brain/app/linker.py
"""Automatic memory graph — v2.0.0.

Derives typed, weighted edges between memories at ingest time. Edges are
DERIVED DATA: fully rebuildable from content/embeddings/tags/metadata via
rebuild_graph(), never precious, and a linker failure must never fail an
ingest (callers wrap in try/except).

Edge kinds
  semantic       embedding KNN above a type-aware threshold (symmetric)
  tag            IDF-weighted tag overlap — rare shared tags count, ubiquitous
                 ones don't (symmetric)
  reference      supersession trail, shared source, explicit UUID mentions
                 (directed except same_source)
  session_chain  each session/handover links to the previous one in the same
                 project — a chronological spine per project (directed)

Pairwise combined weight = noisy-OR over kinds: 1 - prod(1 - w_k).
Computed in Python (not SQL) so we don't depend on SQLite math functions.
"""
import json
import logging
import math
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .storage import DB_PATH
from .vector import vec_search, _connect_vec

logger = logging.getLogger(__name__)

GRAPH_ENABLED_ENV = "MEMORYBRAIN_GRAPH_ENABLED"

# Cosine-similarity floor for a semantic *link* — deliberately below the
# supersession thresholds in ingest_pipeline (near-duplicates supersede;
# merely-related memories link).
THETA_LINK = {
    "fact": 0.62, "note": 0.62, "reference": 0.62,
    "session": 0.58, "handover": 0.58,
    "file": 0.66,
}
_THETA_DEFAULT = 0.62
K_SEMANTIC = 12          # KNN candidates fetched
MAX_SEMANTIC = 8         # strongest kept
MAX_TAG = 6
TAG_W_MIN = 0.25
MAX_SAME_SOURCE = 5

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

_SYMMETRIC_KINDS = {"semantic", "tag"}


def graph_enabled() -> bool:
    return os.getenv(GRAPH_ENABLED_ENV, "true").strip().lower() != "false"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_tags(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw or "[]")
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# ------------------------------------------------------------- edge builders

def _semantic_edges(entry_id: str, entry_type: str, embedding: list[float],
                    db_path: Path, allowed_ids: Optional[set] = None) -> list[dict]:
    theta_self = THETA_LINK.get(entry_type, _THETA_DEFAULT)
    candidates = vec_search(embedding, n_results=K_SEMANTIC + 1,
                            filters={"status": "active"}, db_path=db_path)
    edges = []
    for c in candidates:
        if c["id"] == entry_id:
            continue
        if allowed_ids is not None and c["id"] not in allowed_ids:
            continue
        sim = 1.0 - c["distance"]
        theta = max(theta_self,
                    THETA_LINK.get(c["metadata"].get("type", ""), _THETA_DEFAULT))
        if sim < theta:
            continue
        w = max(0.05, min(1.0, (sim - theta) / (0.95 - theta)))
        edges.append({"src": entry_id, "dst": c["id"], "kind": "semantic",
                      "weight": round(w, 4), "directed": 0,
                      "meta": {"cos_sim": round(sim, 4)}})
    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges[:MAX_SEMANTIC]


def _tag_edges(entry_id: str, entry_tags: list[str], db_path: Path,
               allowed_ids: Optional[set] = None) -> list[dict]:
    tags = [t for t in entry_tags if t]
    if not tags:
        return []
    with _conn(db_path) as conn:
        n_live = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE status = 'active'").fetchone()[0]
        df = {r["tag"]: r["df"] for r in conn.execute(
            "SELECT tag, df FROM tag_stats").fetchall()}
        rows = conn.execute(
            """SELECT id, tags FROM memories
               WHERE status = 'active' AND id != ? AND tags != '[]'
               ORDER BY timestamp DESC LIMIT 500""", (entry_id,)).fetchall()

    def idf(t: str) -> float:
        return math.log((n_live + 1) / (df.get(t, 0) + 1)) + 0.0001

    t_a = set(tags)
    edges = []
    for r in rows:
        if allowed_ids is not None and r["id"] not in allowed_ids:
            continue
        t_b = set(_parse_tags(r["tags"]))
        shared = t_a & t_b
        if not shared:
            continue
        # require at least one *discriminating* shared tag
        if not any(df.get(t, 0) <= max(1, n_live / 4) for t in shared):
            continue
        w = sum(idf(t) for t in shared) / sum(idf(t) for t in (t_a | t_b))
        if w < TAG_W_MIN:
            continue
        edges.append({"src": entry_id, "dst": r["id"], "kind": "tag",
                      "weight": round(min(w, 1.0), 4), "directed": 0,
                      "meta": {"shared_tags": sorted(shared)}})
    edges.sort(key=lambda e: e["weight"], reverse=True)
    return edges[:MAX_TAG]


def _reference_edges(entry_id: str, content: str, source: str,
                     superseded_ids: list[str], db_path: Path,
                     allowed_ids: Optional[set] = None) -> list[dict]:
    edges = []
    for old_id in superseded_ids:
        edges.append({"src": entry_id, "dst": old_id, "kind": "reference",
                      "weight": 1.0, "directed": 1,
                      "meta": {"reason": "supersedes"}})
    with _conn(db_path) as conn:
        # explicit UUID mentions in content
        existing = set()
        mentioned = {m.lower() for m in _UUID_RE.findall(content or "")} - {entry_id}
        for mid in list(mentioned)[:20]:
            row = conn.execute("SELECT id FROM memories WHERE id = ?", (mid,)).fetchone()
            if row:
                existing.add(row["id"])
        for mid in existing:
            if allowed_ids is not None and mid not in allowed_ids:
                continue
            edges.append({"src": entry_id, "dst": mid, "kind": "reference",
                          "weight": 1.0, "directed": 1,
                          "meta": {"reason": "id_mention"}})
        # shared source document
        if source:
            rows = conn.execute(
                """SELECT id FROM memories
                   WHERE source = ? AND id != ? AND status = 'active'
                   ORDER BY timestamp DESC LIMIT ?""",
                (source, entry_id, MAX_SAME_SOURCE)).fetchall()
            for r in rows:
                if allowed_ids is not None and r["id"] not in allowed_ids:
                    continue
                edges.append({"src": entry_id, "dst": r["id"], "kind": "reference",
                              "weight": 0.9, "directed": 0,
                              "meta": {"reason": "same_source"}})
    return edges


def _session_chain_edge(entry_id: str, entry_type: str, project: str,
                        timestamp: str, db_path: Path,
                        allowed_ids: Optional[set] = None) -> list[dict]:
    if entry_type not in ("session", "handover"):
        return []
    with _conn(db_path) as conn:
        row = conn.execute(
            """SELECT id FROM memories
               WHERE project = ? AND type IN ('session','handover')
                 AND id != ? AND timestamp < ?
               ORDER BY timestamp DESC LIMIT 1""",
            (project, entry_id, timestamp)).fetchone()
    if not row:
        return []
    if allowed_ids is not None and row["id"] not in allowed_ids:
        return []
    return [{"src": entry_id, "dst": row["id"], "kind": "session_chain",
             "weight": 1.0, "directed": 1, "meta": {}}]


# --------------------------------------------------------------- persistence

def _write_edges(edges: list[dict], db_path: Path):
    if not edges:
        return
    now = _now()
    with _conn(db_path) as conn:
        for e in edges:
            src, dst = e["src"], e["dst"]
            if e["kind"] in _SYMMETRIC_KINDS and src > dst:
                src, dst = dst, src
            conn.execute(
                """INSERT OR REPLACE INTO memory_links
                   (src_id, dst_id, kind, weight, directed, meta, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (src, dst, e["kind"], e["weight"], e["directed"],
                 json.dumps(e.get("meta", {})), now))
        conn.commit()


def combined_weights(node_id: str, db_path: Path) -> dict[str, float]:
    """Noisy-OR combined weight per neighbour of node_id."""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT dst_id, weight FROM memory_links_all WHERE src_id = ?",
            (node_id,)).fetchall()
    acc: dict[str, float] = {}
    for r in rows:
        acc[r["dst_id"]] = acc.get(r["dst_id"], 1.0) * (1.0 - min(r["weight"], 0.999))
    return {k: round(1.0 - v, 6) for k, v in acc.items()}


def _update_degrees(node_ids: set[str], db_path: Path):
    with _conn(db_path) as conn:
        for nid in node_ids:
            degree = sum(combined_weights(nid, db_path).values())
            conn.execute(
                "UPDATE memories SET link_degree = ?, linked_at = ? WHERE id = ?",
                (round(degree, 4), _now(), nid))
        conn.commit()


def _bump_tag_stats(tags: list[str], db_path: Path):
    if not tags:
        return
    with _conn(db_path) as conn:
        for t in set(tags):
            conn.execute(
                """INSERT INTO tag_stats (tag, df) VALUES (?, 1)
                   ON CONFLICT(tag) DO UPDATE SET df = df + 1""", (t,))
        conn.commit()


# ---------------------------------------------------------------- public API

def link_new_memory(entry, embedding: list[float],
                    superseded_ids: Optional[list[str]] = None,
                    db_path: Path = None,
                    allowed_ids: Optional[set] = None) -> list[dict]:
    """Derive and persist all edges for a freshly ingested memory."""
    if not graph_enabled():
        return []
    db_path = db_path or DB_PATH
    ts = entry.timestamp.isoformat() if hasattr(entry.timestamp, "isoformat") else str(entry.timestamp)

    edges = _semantic_edges(entry.id, entry.type, embedding, db_path, allowed_ids)
    edges += _tag_edges(entry.id, entry.tags or [], db_path, allowed_ids)
    edges += _reference_edges(entry.id, entry.content, entry.source or "",
                              superseded_ids or [], db_path, allowed_ids)
    edges += _session_chain_edge(entry.id, entry.type, entry.project, ts,
                                 db_path, allowed_ids)

    _write_edges(edges, db_path)
    touched = {e["src"] for e in edges} | {e["dst"] for e in edges} | {entry.id}
    _update_degrees(touched, db_path)
    _bump_tag_stats(entry.tags or [], db_path)
    return edges


def rebuild_graph(db_path: Path = None) -> dict:
    """Drop and recompute every edge from scratch, oldest memory first.
    Safe to run any time — edges are cache, not truth."""
    from .models import MemoryEntry  # local import to avoid cycles

    db_path = db_path or DB_PATH
    with _conn(db_path) as conn:
        conn.execute("DELETE FROM memory_links")
        conn.execute("DELETE FROM tag_stats")
        conn.execute("UPDATE memories SET link_degree = 0, linked_at = NULL")
        conn.commit()
        rows = conn.execute(
            """SELECT m.id, m.content, m.summary, m.type, m.project, m.tags,
                      m.source, m.timestamp, m.supersedes
               FROM memories m WHERE m.status = 'active'
               ORDER BY m.timestamp ASC""").fetchall()

    processed: set[str] = set()
    edge_count: dict[str, int] = {}
    import struct
    for r in rows:
        with _connect_vec(db_path) as vconn:
            vrow = vconn.execute(
                "SELECT embedding FROM vec_memories WHERE memory_id = ?",
                (r["id"],)).fetchone()
        embedding = (list(struct.unpack(f"{len(vrow[0]) // 4}f", vrow[0]))
                     if vrow else None)

        class _E:  # lightweight duck-typed entry
            pass
        e = _E()
        e.id, e.type, e.project = r["id"], r["type"], r["project"]
        e.content, e.source = r["content"], r["source"]
        e.tags, e.timestamp = _parse_tags(r["tags"]), r["timestamp"]

        edges = []
        if embedding:
            edges += _semantic_edges(e.id, e.type, embedding, db_path, processed)
        edges += _tag_edges(e.id, e.tags, db_path, processed)
        edges += _reference_edges(e.id, e.content, e.source or "",
                                  [r["supersedes"]] if r["supersedes"] else [],
                                  db_path, processed)
        edges += _session_chain_edge(e.id, e.type, e.project, e.timestamp,
                                     db_path, processed)
        _write_edges(edges, db_path)
        _bump_tag_stats(e.tags, db_path)
        for edge in edges:
            edge_count[edge["kind"]] = edge_count.get(edge["kind"], 0) + 1
        processed.add(e.id)

    _update_degrees(processed, db_path)
    return {"memories": len(rows), "edges_by_kind": edge_count,
            "total_edges": sum(edge_count.values())}
