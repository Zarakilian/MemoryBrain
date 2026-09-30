# brain/app/graph_queries.py
"""Read-only graph queries — shared by the MCP tools and the web UI."""
import json
import sqlite3
from pathlib import Path
from typing import Optional

from .storage import DB_PATH
from .linker import combined_weights

VALID_KINDS = {"semantic", "tag", "reference", "session_chain", "entity"}

FOLDER_PULL_CAP = 150


def _norm_folder(rel: str) -> str:
    return (rel or "").replace("\\", "/").strip().strip("/").lower()


def _file_layer(conn, mem_nodes: list[dict], mem_ids: set, project: Optional[str],
                folders: list[str]) -> tuple[list[dict], list[dict], list[dict], bool]:
    """Files named by the visible memories, plus everything under each
    requested folder (capped), plus the bound folders of the projects in
    scope. Returns (file_nodes, folder_nodes, edges, files_truncated)."""
    refs = [dict(r) for r in conn.execute(
        """SELECT src_id, dst_id, weight FROM file_links
           WHERE kind = 'file_ref' AND src_kind = 'memory' AND dst_kind = 'file'""").fetchall()
        if r["src_id"] in mem_ids]
    file_ids = {r["dst_id"] for r in refs}
    files_truncated = False
    for folder in folders:
        fci = _norm_folder(folder)
        if not fci:
            continue
        rows = conn.execute(
            """SELECT file_id FROM workspace_files WHERE status = 'active'
               AND (rel_path_ci = ? OR substr(rel_path_ci, 1, ?) = ?)
               ORDER BY ref_degree DESC, mtime DESC LIMIT ?""",
            (fci, len(fci) + 1, fci + "/", FOLDER_PULL_CAP + 1)).fetchall()
        if len(rows) > FOLDER_PULL_CAP:
            files_truncated = True
            rows = rows[:FOLDER_PULL_CAP]
        file_ids |= {r["file_id"] for r in rows}

    bound = [dict(r) for r in conn.execute(
        """SELECT root_id, rel_path, rel_path_ci, project, role, label
           FROM project_folders WHERE project != ''""").fetchall()]

    def owner(root_id: str, rel_ci: str):
        best = None
        for f in bound:
            if f["root_id"] != root_id:
                continue
            p = f["rel_path_ci"]
            if p == "" or rel_ci == p or rel_ci.startswith(p + "/"):
                if best is None or len(p) > len(best["rel_path_ci"]):
                    best = f
        return best

    file_rows: list[dict] = []
    ids = sorted(file_ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        file_rows += [dict(r) for r in conn.execute(
            f"""SELECT file_id, root_id, rel_path, rel_path_ci, ext, mtime, title, ref_degree
                FROM workspace_files WHERE status = 'active'
                AND file_id IN ({",".join("?" * len(chunk))})""", chunk).fetchall()]

    scope = {n["project"] for n in mem_nodes if n.get("project")}
    if project:
        scope = {project}
    file_nodes, in_folder, folder_deg = [], [], {}
    for r in file_rows:
        o = owner(r["root_id"], r["rel_path_ci"])
        proj = o["project"] if o else ""
        node = {"id": "file:" + r["file_id"], "kind": "file",
                "label": r["rel_path"].rsplit("/", 1)[-1], "path": r["rel_path"],
                "project": proj, "ext": r["ext"] or "", "degree": r["ref_degree"] or 0,
                "mtime": r["mtime"] or "", "title": r["title"] or "",
                "importance": 3, "timestamp": r["mtime"] or ""}
        file_nodes.append(node)
        if o:
            fkey = "folder:" + o["root_id"] + "/" + o["rel_path_ci"]
            in_folder.append({"src": node["id"], "dst": fkey, "w": 0.5, "kinds": ["in_folder"]})
            folder_deg[fkey] = folder_deg.get(fkey, 0.0) + (r["ref_degree"] or 0)

    folder_nodes, folder_ids = [], set()
    for f in bound:
        if f["project"] not in scope:
            continue
        fci = f["rel_path_ci"]
        n = conn.execute(
            """SELECT COUNT(*) FROM workspace_files WHERE status = 'active' AND root_id = ?
               AND (? = '' OR rel_path_ci = ? OR substr(rel_path_ci, 1, ?) = ?)""",
            (f["root_id"], fci, fci, len(fci) + 1, fci + "/")).fetchone()[0]
        fkey = "folder:" + f["root_id"] + "/" + fci
        folder_ids.add(fkey)
        folder_nodes.append({"id": fkey, "kind": "folder",
                             "label": f["rel_path"].rsplit("/", 1)[-1] or f["root_id"],
                             "path": f["rel_path"], "project": f["project"], "role": f["role"],
                             "file_count": n, "degree": round(folder_deg.get(fkey, 0.0), 3),
                             "importance": 3, "timestamp": ""})
    edges = [{"src": r["src_id"], "dst": "file:" + r["dst_id"], "w": round(r["weight"], 4),
              "kinds": ["file_ref"]} for r in refs if r["dst_id"] in file_ids]
    edges += [e for e in in_folder if e["dst"] in folder_ids]
    return file_nodes, folder_nodes, edges, files_truncated


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
              db_path: Path = None, include_files: bool = False,
              folders: Optional[list[str]] = None) -> dict:
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

    for n in nodes:
        n["kind"] = "memory"
    result = {"nodes": nodes, "edges": edges, "truncated": truncated}
    if include_files:
        with _conn(db_path) as conn:
            file_nodes, folder_nodes, fedges, ftrunc = _file_layer(
                conn, nodes, ids, project, folders or [])
        result["nodes"] = nodes + folder_nodes + file_nodes
        result["edges"] = edges + fedges
        result["files_truncated"] = ftrunc
    return result
