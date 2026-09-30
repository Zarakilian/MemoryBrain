"""Resolve path tokens in memory text to indexed files; derive file_ref edges.

Edges are cache. rebuild_file_links() recomputes every memory->file edge from
content. A failure here must never fail an ingest (the linker wraps the call).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .. import storage as _st
from . import store as ws
from .paths import ci, extract_path_tokens, is_path_shaped, normalise_rel

logger = logging.getLogger(__name__)

WEIGHTS = {"exact": 1.0, "exact_global": 1.0, "suffix": 0.9,
           "basename": 0.8, "basename_global": 0.6, "dangling": 0.1}
MAX_FILE_EDGES = 25
MAX_CANDIDATES = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _owner(row: dict, bound: list[dict]) -> str | None:
    """Project of the deepest bound folder above the row, in the row's root, or None.

    Same rule as store.owner_project: a folder bound at the root (rel_path_ci '')
    owns every file in that root, and the longest matching prefix wins, so a file
    under a nested folder bound to another project belongs to that other project.
    """
    rp = row["rel_path_ci"]
    project, depth = None, -1
    for f in bound:
        if f["root_id"] != row["root_id"]:
            continue
        fp = f["rel_path_ci"]
        if (fp == "" or rp == fp or rp.startswith(fp + "/")) and len(fp) > depth:
            project, depth = f["project"], len(fp)
    return project


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _result(how: str, row: dict | None = None, candidates: list[dict] | None = None) -> dict:
    # rel_path is None on a miss; resolve_token fills in the token it resolved.
    return {"file_id": row["file_id"] if row else None, "how": how,
            "weight": WEIGHTS.get(how, 0.0), "rel_path": row["rel_path"] if row else None,
            "candidates": [c["rel_path"] for c in (candidates or [])][:MAX_CANDIDATES]}


def _rows(conn, where: str, params: tuple) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM workspace_files WHERE status = 'active' AND " + where, params).fetchall()]


def _pick(rows: list[dict], how: str, how_global: str, project: str, bound: list[dict]) -> dict | None:
    """One in-project row wins the tier; several dangle with those candidates. With none
    in-project, one row anywhere wins the global tier; several dangle. No rows: None."""
    if not rows:
        return None
    mine = [r for r in rows if _owner(r, bound) == project]
    if len(mine) == 1:
        return _result(how, mine[0])
    if len(mine) > 1:
        return _result("dangling", None, mine)
    if len(rows) == 1:
        return _result(how_global, rows[0])
    return _result("dangling", None, rows)


def _suffix_tiers(conn, rel_ci: str, project: str, bound: list[dict]) -> dict:
    """exact and exact_global, then the '%/rel_ci' and '%rel_ci' suffix searches. The first
    tier with any rows decides; when none has rows the result dangles with no candidates."""
    res = _pick(_rows(conn, "rel_path_ci = ?", (rel_ci,)), "exact", "exact_global", project, bound)
    if res:
        return res
    for pattern in ("%/" + _like_escape(rel_ci), "%" + _like_escape(rel_ci)):
        res = _pick(_rows(conn, "rel_path_ci LIKE ? ESCAPE '\\'", (pattern,)), "suffix", "suffix", project, bound)
        if res:
            return res
    return _result("dangling")


def _basename_tiers(conn, base_ci: str, project: str, bound: list[dict]) -> dict:
    """basename and basename_global; a bare filename never uses the exact tiers."""
    res = _pick(_rows(conn, "basename_ci = ?", (base_ci,)), "basename", "basename_global", project, bound)
    return res or _result("dangling")


def _has_ws(s: str) -> bool:
    return any(ch.isspace() for ch in s)


def _rescue_pieces(rel: str) -> list[str]:
    """The trailing '/' segments that carry no whitespace, headed by the last word of the
    first segment (walking back) that does. Original case. A real path only carries spaces
    inside real folder or file names; prose carries them everywhere, so these are the path part."""
    picked: list[str] = []
    for seg in reversed(rel.split("/")):
        if _has_ws(seg):
            words = seg.split()
            if words:
                picked.insert(0, words[-1])
            break
        picked.insert(0, seg)
    return picked


def resolve_token(token: str, project: str, db_path: Path) -> dict:
    # Bare filenames skip the exact tiers; a slash-suffix miss falls back to an endswith match.
    roots = [r["abs_path"] for r in ws.list_roots(db_path)]
    rel = normalise_rel(token, roots)
    rel_ci = ci(rel)
    bound = [f for f in ws.list_folders(db_path) if f["project"]]

    def finish(res: dict, matched: str) -> dict:
        if res["file_id"] is None:
            res["rel_path"] = rel
        res["matched"] = matched
        return res

    with _st._connect(db_path) as conn:
        if is_path_shaped(rel):
            res = _suffix_tiers(conn, rel_ci, project, bound)
        else:
            res = _basename_tiers(conn, rel_ci, project, bound)
        if res["file_id"] or res["candidates"] or not _has_ws(rel):
            return finish(res, rel)
        # Rescue: every tier missed with no candidates and the token carries whitespace, so it
        # is prose glued to a path. Try the clean trailing segments as a suffix, then the last
        # word alone as a bare filename. A token with no whitespace is never rescued: a clean
        # path that dangles stays dangling with its candidates.
        pieces = _rescue_pieces(rel)
        if not pieces:
            return finish(res, rel)
        if len(pieces) >= 2:
            joined = "/".join(pieces)
            hit = _suffix_tiers(conn, ci(joined), project, bound)
            if hit["file_id"]:
                return finish(hit, joined)
        word = pieces[-1]
        return finish(_basename_tiers(conn, ci(word), project, bound), word)


def file_ref_edges(memory_id: str, project: str, text: str, db_path: Path) -> list[dict]:
    roots = [r["abs_path"] for r in ws.list_roots(db_path)]
    best: dict[str, dict] = {}
    for token in extract_path_tokens(text or ""):
        res = resolve_token(token, project, db_path)
        rel = normalise_rel(token, roots)
        dst_kind = "file" if res["file_id"] else "dangling"
        dst_id = res["file_id"] or rel
        key = dst_kind + ":" + dst_id.lower()
        meta = {"token": token, "how": res["how"], "candidates": res["candidates"]}
        if res["matched"] != rel:
            meta["matched"] = res["matched"]   # a rescued token: the piece that was actually searched
        edge = {"src_kind": "memory", "src_id": memory_id, "dst_kind": dst_kind, "dst_id": dst_id,
                "kind": "file_ref", "weight": res["weight"], "meta": meta}
        if key not in best or edge["weight"] > best[key]["weight"]:
            best[key] = edge
    edges = sorted(best.values(), key=lambda e: e["weight"], reverse=True)
    return edges[:MAX_FILE_EDGES]


def write_file_links(edges: list[dict], db_path: Path) -> None:
    if not edges:
        return
    now = _now()
    with _st._connect(db_path) as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO file_links (src_kind, src_id, dst_kind, dst_id, kind, weight, meta, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(e["src_kind"], e["src_id"], e["dst_kind"], e["dst_id"], e["kind"], e["weight"],
              json.dumps(e.get("meta") or {}), now) for e in edges])
        conn.commit()


def _infer_bindings(project: str, edges: list[dict], db_path: Path) -> None:
    """A memory in project P naming a path under an UNBOUND top folder binds it (how='memory')."""
    roots = ws.list_roots(db_path)
    if not roots or not project:
        return
    with _st._connect(db_path) as conn:
        for e in edges:
            token = e["meta"].get("token", "")
            if not is_path_shaped(token):
                continue
            if e["dst_kind"] == "file":
                f = ws.get_file(db_path, file_id=e["dst_id"])
                if not f:
                    continue
                root_id, top = f["root_id"], f["folder"]
            else:
                rel = normalise_rel(token, [r["abs_path"] for r in roots])
                row = conn.execute(
                    "SELECT root_id, folder FROM workspace_files WHERE folder = ? COLLATE NOCASE LIMIT 1",
                    (rel.split("/", 1)[0],)).fetchone()
                if not row:
                    continue
                root_id, top = row["root_id"], row["folder"]
            cur = ws.owner_project(root_id, top, db_path)
            if cur is None:
                ws.bind_folder(root_id, top, project, "memory", db_path)
            elif cur["rel_path_ci"] == ci(top) and cur["project"] == project and cur["how"] == "memory":
                ws.bind_folder(root_id, top, project, "memory", db_path)  # reinforce


def link_memory_files(entry, db_path: Path) -> list[dict]:
    text = f"{getattr(entry, 'summary', '') or ''}\n{entry.content or ''}"
    edges = file_ref_edges(entry.id, entry.project, text, db_path)
    write_file_links(edges, db_path)
    ws.update_ref_degrees({e["dst_id"] for e in edges if e["dst_kind"] == "file"}, db_path)
    try:
        _infer_bindings(entry.project, edges, db_path)
    except Exception:
        logger.warning("folder inference failed for %s", entry.id, exc_info=True)
    return edges


def rebuild_file_links(db_path: Path) -> dict:
    with _st._connect(db_path) as conn:
        conn.execute("DELETE FROM file_links WHERE src_kind = 'memory' AND kind = 'file_ref'")
        conn.execute("UPDATE workspace_files SET ref_degree = 0")
        conn.commit()
        rows = conn.execute(
            """SELECT id, project, summary, content FROM memories
               WHERE status = 'active' ORDER BY timestamp ASC""").fetchall()
    n_edges = n_dangling = 0
    touched: set[str] = set()
    for r in rows:
        edges = file_ref_edges(r["id"], r["project"], f"{r['summary'] or ''}\n{r['content'] or ''}", db_path)
        write_file_links(edges, db_path)
        n_edges += len(edges)
        n_dangling += sum(1 for e in edges if e["dst_kind"] == "dangling")
        touched |= {e["dst_id"] for e in edges if e["dst_kind"] == "file"}
    ws.update_ref_degrees(touched, db_path)
    return {"memories": len(rows), "edges": n_edges, "dangling": n_dangling, "files_touched": len(touched)}
