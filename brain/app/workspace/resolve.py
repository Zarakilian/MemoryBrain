"""Resolve path tokens in memory text to indexed files; derive file_ref edges.

Edges derived from prose are cache: rebuild_file_links() recomputes them from
content. Explicit refs (add_memory refs=[...], meta.explicit) are what an agent
named on purpose; no relink or rebuild removes or rewrites them. A failure here
must never fail an ingest (the linker wraps the call).
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


_WRITE_LINK = """INSERT INTO file_links (src_kind, src_id, dst_kind, dst_id, kind, weight, meta, created_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                  ON CONFLICT (src_kind, src_id, dst_kind, dst_id, kind) DO UPDATE
                  SET weight = excluded.weight, meta = excluded.meta, created_at = excluded.created_at
                  WHERE COALESCE(json_extract(file_links.meta, '$.explicit'), 0) = 0"""
_WRITE_EXPLICIT = """INSERT OR REPLACE INTO file_links
                     (src_kind, src_id, dst_kind, dst_id, kind, weight, meta, created_at)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""


def write_file_links(edges: list[dict], db_path: Path, explicit: bool = False) -> None:
    """Upsert edges. A derived edge never overwrites an explicit one to the same
    target; an explicit edge overwrites whatever is there."""
    if not edges:
        return
    now = _now()
    with _st._connect(db_path) as conn:
        conn.executemany(
            _WRITE_EXPLICIT if explicit else _WRITE_LINK,
            [(e["src_kind"], e["src_id"], e["dst_kind"], e["dst_id"], e["kind"], e["weight"],
              json.dumps(e.get("meta") or {}), now) for e in edges])
        conn.commit()


# ── explicit refs (add_memory refs=[...]) ─────────────────────────────────

REF_KINDS = ("file", "directory", "url", "task", "service")
MAX_REFS = 25
MAX_REF_CHARS = 500


def clean_refs(refs) -> list[dict]:
    """Validate add_memory refs: at most 25 objects {path, kind}. Raises ValueError."""
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise ValueError("refs must be a list of {path, kind} objects")
    if len(refs) > MAX_REFS:
        raise ValueError(f"refs: at most {MAX_REFS} per memory, got {len(refs)}")
    out = []
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError("refs: each ref must be an object with path and kind")
        path = str(ref.get("path") or "").strip()
        kind = str(ref.get("kind") or "").strip().lower()
        if not path:
            raise ValueError("refs: path must not be empty")
        if len(path) > MAX_REF_CHARS:
            raise ValueError(f"refs: a path is longer than {MAX_REF_CHARS} characters")
        if kind not in REF_KINDS:
            raise ValueError(f"refs: kind must be one of {', '.join(REF_KINDS)}")
        out.append({"path": path, "kind": kind})
    return out


def explicit_ref_edges(memory_id: str, project: str, refs: list[dict], db_path: Path) -> list[dict]:
    """A file ref resolves like a path in prose and is kept even when it dangles.
    Directory, url, task and service refs are stored dangling, with their kind."""
    roots = [r["abs_path"] for r in ws.list_roots(db_path)]
    best: dict[str, dict] = {}
    for ref in refs:
        path, kind = ref["path"], ref["kind"]
        meta = {"token": path, "explicit": True, "ref_kind": kind}
        if kind == "file":
            res = resolve_token(path, project, db_path)
            dst_kind = "file" if res["file_id"] else "dangling"
            dst_id = res["file_id"] or normalise_rel(path, roots)
            weight = res["weight"]
            meta.update(how=res["how"], candidates=res["candidates"])
        else:
            dst_kind, dst_id, weight = "dangling", path, 1.0
        key = dst_kind + ":" + (dst_id.lower() if kind == "file" else dst_id)
        best[key] = {
            "src_kind": "memory", "src_id": memory_id, "dst_kind": dst_kind, "dst_id": dst_id,
            "kind": "file_ref", "weight": weight, "meta": meta}
    return list(best.values())


def link_explicit_refs(memory_id: str, project: str, refs: list[dict], db_path: Path) -> dict:
    """Store explicit refs as file links. Returns {"file": n, "dangling": n}."""
    edges = explicit_ref_edges(memory_id, project, refs, db_path)
    write_file_links(edges, db_path, explicit=True)
    ws.update_ref_degrees({e["dst_id"] for e in edges if e["dst_kind"] == "file"}, db_path)
    return {"file": sum(1 for e in edges if e["dst_kind"] == "file"),
            "dangling": sum(1 for e in edges if e["dst_kind"] == "dangling")}


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
        conn.execute("""DELETE FROM file_links WHERE src_kind = 'memory' AND kind = 'file_ref'
                        AND COALESCE(json_extract(meta, '$.explicit'), 0) = 0""")
        explicit = {r[0] for r in conn.execute(
            """SELECT dst_id FROM file_links WHERE src_kind = 'memory' AND kind = 'file_ref'
               AND dst_kind = 'file'""")}
        waiting = conn.execute(
            """SELECT fl.src_id, fl.dst_id, fl.meta, m.project FROM file_links fl
               JOIN memories m ON m.id = fl.src_id
               WHERE fl.src_kind = 'memory' AND fl.kind = 'file_ref' AND fl.dst_kind = 'dangling'
                 AND COALESCE(json_extract(fl.meta, '$.explicit'), 0) = 1
                 AND COALESCE(json_extract(fl.meta, '$.ref_kind'), 'file') = 'file'""").fetchall()
        conn.execute("UPDATE workspace_files SET ref_degree = 0")
        conn.commit()
        rows = conn.execute(
            """SELECT id, project, summary, content FROM memories
               WHERE status = 'active' ORDER BY timestamp ASC""").fetchall()
    n_edges = n_dangling = 0
    touched: set[str] = set(explicit)  # explicit refs survive, so their files keep a degree
    # An explicit file ref named before its file was indexed resolves now.
    explicit_resolved = 0
    for row in waiting:
        meta = json.loads(row["meta"] or "{}")
        res = resolve_token(meta.get("token") or row["dst_id"], row["project"], db_path)
        if not res["file_id"]:
            continue
        with _st._connect(db_path) as conn:
            conn.execute("""DELETE FROM file_links WHERE src_kind = 'memory' AND src_id = ?
                            AND dst_kind = 'dangling' AND dst_id = ? AND kind = 'file_ref'""",
                         (row["src_id"], row["dst_id"]))
            conn.commit()
        meta.update(how=res["how"], candidates=res["candidates"])
        write_file_links([{"src_kind": "memory", "src_id": row["src_id"], "dst_kind": "file",
                           "dst_id": res["file_id"], "kind": "file_ref",
                           "weight": res["weight"], "meta": meta}], db_path, explicit=True)
        touched.add(res["file_id"])
        explicit_resolved += 1
    for r in rows:
        edges = file_ref_edges(r["id"], r["project"], f"{r['summary'] or ''}\n{r['content'] or ''}", db_path)
        write_file_links(edges, db_path)
        n_edges += len(edges)
        n_dangling += sum(1 for e in edges if e["dst_kind"] == "dangling")
        touched |= {e["dst_id"] for e in edges if e["dst_kind"] == "file"}
    ws.update_ref_degrees(touched, db_path)
    return {"memories": len(rows), "edges": n_edges, "dangling": n_dangling,
            "files_touched": len(touched), "explicit_resolved": explicit_resolved}
