"""Workspace layer SQL: roots, folder bindings, files, manifests, queries.

Every function takes db_path explicitly. Callers pass `_st.DB_PATH` at call
time so the tmp_db fixture's monkeypatch is honoured.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .. import storage as _st
from ..models import PROJECT_SLUG_RE, Project
from ..redact import scrub, strip_url_userinfo
from .paths import ci, split_ext

RANK = {"marker": 5, "tool": 5, "cwd": 4, "init": 3, "memory": 2}
CONF = {"marker": 1.0, "tool": 1.0, "cwd": 1.0, "init": 0.8, "memory": 0.6}
MEMORY_CONF_CAP = 0.8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_rel(rel_path: str) -> str:
    r = rel_path.replace("\\", "/").strip().strip("/")
    while r.startswith("./"):
        r = r[2:]
    return r


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row(r) -> Optional[dict]:
    return dict(r) if r is not None else None


# ── roots ──────────────────────────────────────────────────────────────────

def upsert_root(root_id: str, machine: str, abs_path: str, db_path: Path) -> dict:
    now = _now()
    with _st._connect(db_path) as conn:
        conn.execute(
            """INSERT INTO workspace_roots (root_id, machine, abs_path, first_seen, last_seen)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(root_id, machine) DO UPDATE SET
                   abs_path = excluded.abs_path, last_seen = excluded.last_seen""",
            (root_id, machine, abs_path, now, now))
        conn.commit()
        return _row(conn.execute(
            "SELECT * FROM workspace_roots WHERE root_id = ? AND machine = ?",
            (root_id, machine)).fetchone())


def list_roots(db_path: Path) -> list[dict]:
    with _st._connect(db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM workspace_roots ORDER BY root_id, machine").fetchall()]


def resolve_abs_path(path: str, db_path: Path) -> Optional[tuple[str, str]]:
    """Absolute or root-relative path -> (root_id, rel_path) or None."""
    p = path.replace("\\", "/").strip().rstrip("/")
    low = p.lower()
    roots = list_roots(db_path)
    for r in roots:
        ra = r["abs_path"].replace("\\", "/").rstrip("/").lower()
        if low == ra:
            return (r["root_id"], "")
        if low.startswith(ra + "/"):
            return (r["root_id"], p[len(ra) + 1:])
    is_abs = (len(p) > 1 and p[1] == ":") or p.startswith("/")
    root_ids = sorted({r["root_id"] for r in roots})
    if not is_abs and len(root_ids) == 1:
        return (root_ids[0], _norm_rel(p))
    return None


# ── folder bindings ────────────────────────────────────────────────────────

def _get_folder(conn, root_id: str, rel_ci: str):
    return conn.execute(
        "SELECT * FROM project_folders WHERE root_id = ? AND rel_path_ci = ?",
        (root_id, rel_ci)).fetchone()


def _ensure_project(project: str, db_path: Path) -> None:
    """Create the projects row for a slug that has none, so a bound folder always has
    a project the UI can list. An existing row is left untouched (its last_activity too)."""
    if _st.get_project(project, db_path=db_path) is None:
        _st.upsert_project(Project(slug=project, name=project.replace("-", " ").title()), db_path=db_path)


def bind_folder(root_id: str, rel_path: str, project: str, how: str, db_path: Path, *,
                label: str = "", role: str = "primary", remote_url: str = "",
                confirmed: Optional[int] = None, confidence: Optional[float] = None) -> dict:
    if how not in RANK:
        raise ValueError(f"unknown binding source: {how}")
    if not PROJECT_SLUG_RE.match(project or ""):
        raise ValueError(f"invalid project slug: {project!r}")
    remote_url = strip_url_userinfo(remote_url)
    label = scrub(label or "")
    _ensure_project(project, db_path)
    rel = _norm_rel(rel_path)
    rel_ci = ci(rel)
    depth = len([s for s in rel.split("/") if s]) if rel else 0
    new_conf = CONF[how] if confidence is None else min(float(confidence), CONF[how])
    new_confirmed = (1 if how in ("marker", "tool") else 0) if confirmed is None else int(bool(confirmed))
    now = _now()
    with _st._connect(db_path) as conn:
        existing = _get_folder(conn, root_id, rel_ci)
        if existing is None or existing["project"] == "":
            conn.execute(
                """INSERT INTO project_folders (root_id, rel_path, rel_path_ci, depth, project, label,
                       role, remote_url, how, confidence, confirmed, evidence_count, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                   ON CONFLICT(root_id, rel_path_ci) DO UPDATE SET
                       project = excluded.project, label = excluded.label, role = excluded.role,
                       remote_url = CASE WHEN excluded.remote_url != '' THEN excluded.remote_url
                                         ELSE project_folders.remote_url END,
                       how = excluded.how, confidence = excluded.confidence,
                       confirmed = excluded.confirmed, evidence_count = 1, updated_at = excluded.updated_at""",
                (root_id, rel, rel_ci, depth, project, label, role, remote_url,
                 how, new_conf, new_confirmed, now))
            conn.commit()
            return {"written": True, "reason": "inserted",
                    "row": _row(_get_folder(conn, root_id, rel_ci)), "conflict_with": ""}

        if existing["project"] == project:
            evidence = existing["evidence_count"] + (1 if how == "memory" else 0)
            if how == "memory":
                conf = min(MEMORY_CONF_CAP, CONF["memory"] + 0.2 * (evidence - 1))
                conf = max(conf, existing["confidence"])
            else:
                conf = max(existing["confidence"], new_conf)
            new_how = how if RANK[how] > RANK[existing["how"]] else existing["how"]
            conn.execute(
                """UPDATE project_folders SET how = ?, confidence = ?, evidence_count = ?,
                       label = CASE WHEN ? != '' THEN ? ELSE label END,
                       role = CASE WHEN ? != 'primary' THEN ? ELSE role END,
                       remote_url = CASE WHEN ? != '' THEN ? ELSE remote_url END,
                       confirmed = MAX(confirmed, ?), updated_at = ?
                   WHERE root_id = ? AND rel_path_ci = ?""",
                (new_how, round(conf, 4), evidence, label, label, role, role,
                 remote_url, remote_url, new_confirmed, now, root_id, rel_ci))
            conn.commit()
            return {"written": True, "reason": "reinforced",
                    "row": _row(_get_folder(conn, root_id, rel_ci)), "conflict_with": ""}

        # different project: only a higher ranked source may overwrite;
        # equal rank overwrites only for the two human-backed sources.
        wins = RANK[how] > RANK[existing["how"]] or (
            RANK[how] == RANK[existing["how"]] and how in ("marker", "tool"))
        if not wins:
            return {"written": False, "reason": "kept_higher_ranked",
                    "row": _row(existing), "conflict_with": project}
        conn.execute(
            """UPDATE project_folders SET project = ?, label = ?, role = ?, how = ?, confidence = ?,
                   confirmed = ?, evidence_count = 1,
                   remote_url = CASE WHEN ? != '' THEN ? ELSE remote_url END,
                   updated_at = ?
               WHERE root_id = ? AND rel_path_ci = ?""",
            (project, label, role, how, new_conf, new_confirmed, remote_url, remote_url,
             now, root_id, rel_ci))
        conn.commit()
        return {"written": True, "reason": "overwrote_lower_ranked",
                "row": _row(_get_folder(conn, root_id, rel_ci)),
                "conflict_with": existing["project"]}


def record_folder_seen(root_id: str, rel_path: str, remote_url: str, db_path: Path) -> None:
    rel = _norm_rel(rel_path)
    remote_url = strip_url_userinfo(remote_url)
    with _st._connect(db_path) as conn:
        conn.execute(
            """INSERT OR IGNORE INTO project_folders
                   (root_id, rel_path, rel_path_ci, depth, project, remote_url, how,
                    confidence, confirmed, evidence_count, updated_at)
               VALUES (?, ?, ?, ?, '', ?, 'init', 0.0, 0, 0, ?)""",
            (root_id, rel, ci(rel), len([s for s in rel.split("/") if s]), remote_url, _now()))
        conn.commit()


def list_folders(db_path: Path, project: Optional[str] = None,
                 root_id: Optional[str] = None) -> list[dict]:
    sql = "SELECT * FROM project_folders WHERE 1=1"
    params: list[Any] = []
    if project is not None:
        sql += " AND project = ?"
        params.append(project)
    if root_id:
        sql += " AND root_id = ?"
        params.append(root_id)
    sql += " ORDER BY root_id, rel_path_ci"
    with _st._connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows:  # rows stored before v3 may still carry user:token@
        row["remote_url"] = strip_url_userinfo(row.get("remote_url") or "")
    return rows


def owner_project(root_id: str, rel_path: str, db_path: Path) -> Optional[dict]:
    """Longest bound prefix wins. Files never need their own row."""
    rel_ci = ci(_norm_rel(rel_path))
    with _st._connect(db_path) as conn:
        row = conn.execute(
            """SELECT * FROM project_folders
               WHERE root_id = ? AND project != ''
                 AND (rel_path_ci = '' OR rel_path_ci = ?
                      OR substr(?, 1, length(rel_path_ci) + 1) = rel_path_ci || '/')
               ORDER BY length(rel_path_ci) DESC LIMIT 1""",
            (root_id, rel_ci, rel_ci)).fetchone()
    return _row(row)


# ── files and manifests ────────────────────────────────────────────────────

def _file_by(conn, root_id: str, rel_ci: str):
    return conn.execute(
        "SELECT * FROM workspace_files WHERE root_id = ? AND rel_path_ci = ?",
        (root_id, rel_ci)).fetchone()


def apply_manifest(manifest: dict, db_path: Path) -> dict:
    root_id = manifest["root_id"]
    machine = manifest.get("machine", "")
    full = bool(manifest.get("full", False))
    now = _now()
    upsert_root(root_id, machine, manifest.get("abs_path", ""), db_path)
    added = updated = deleted = moved = 0
    seen_ci: set[str] = set()
    new_by_hash: dict[str, str] = {}   # sha256 -> file_id inserted this run
    with _st._connect(db_path) as conn:
        for f in manifest.get("files", []):
            if f.get("title"):
                f = {**f, "title": scrub(str(f["title"]))}
            rel = _norm_rel(f["rel_path"])
            if not rel:
                continue
            rel_ci = ci(rel)
            seen_ci.add(rel_ci)
            sha = f.get("sha256") or ""
            existing = _file_by(conn, root_id, rel_ci)
            if existing is None:
                fid = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO workspace_files (file_id, root_id, rel_path, rel_path_ci, basename_ci,
                           folder, ext, size, mtime, content_hash, title, status, first_seen, last_seen, seen_on)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)""",
                    (fid, root_id, rel, rel_ci, rel_ci.rsplit("/", 1)[-1], rel.split("/", 1)[0],
                     split_ext(rel), int(f.get("size") or 0), f.get("mtime") or now, sha,
                     f.get("title") or "", now, now, json.dumps([machine] if machine else [])))
                added += 1
                if sha and int(f.get("size") or 0) > 0:
                    new_by_hash[sha] = fid   # a zero-byte file's hash is not move evidence
            else:
                seen_on = json.loads(existing["seen_on"] or "[]")
                if machine and machine not in seen_on:
                    seen_on.append(machine)
                conn.execute(
                    """UPDATE workspace_files SET rel_path = ?, folder = ?, size = ?, mtime = ?,
                           content_hash = ?, title = ?, status = 'active', moved_to = NULL,
                           last_seen = ?, seen_on = ?
                       WHERE file_id = ?""",
                    (rel, rel.split("/", 1)[0], int(f.get("size") or 0),
                     f.get("mtime") or existing["mtime"], sha,
                     f.get("title") or existing["title"], now, json.dumps(seen_on), existing["file_id"]))
                updated += 1
        if full:
            gone = conn.execute(
                """SELECT file_id, rel_path_ci, content_hash FROM workspace_files
                   WHERE root_id = ? AND status = 'active'""", (root_id,)).fetchall()
            for g in gone:
                if g["rel_path_ci"] in seen_ci:
                    continue
                target = new_by_hash.get(g["content_hash"] or "__none__")
                if target:
                    conn.execute(
                        "UPDATE workspace_files SET status = 'moved', moved_to = ?, last_seen = ? WHERE file_id = ?",
                        (target, now, g["file_id"]))
                    conn.execute(
                        """INSERT OR IGNORE INTO file_links (src_kind, src_id, dst_kind, dst_id, kind, weight, meta, created_at)
                           SELECT src_kind, src_id, dst_kind, ?, kind, weight, meta, created_at
                           FROM file_links WHERE dst_kind = 'file' AND dst_id = ?""",
                        (target, g["file_id"]))
                    moved += 1
                else:
                    conn.execute(
                        "UPDATE workspace_files SET status = 'deleted', last_seen = ? WHERE file_id = ?",
                        (now, g["file_id"]))
                    deleted += 1
        conn.commit()
    markers_bound = markers_skipped = 0
    for m in manifest.get("markers", []):
        if m.get("project"):
            try:
                r = bind_folder(root_id, m["rel_path"], m["project"], "marker", db_path)
            except ValueError:
                markers_skipped += 1   # a marker naming an invalid slug binds nothing
                continue
            markers_bound += 1 if r["written"] else 0
    return {"root_id": root_id, "machine": machine, "added": added, "updated": updated,
            "deleted": deleted, "moved": moved, "markers_bound": markers_bound,
            "markers_skipped": markers_skipped}


def get_file(db_path: Path, file_id: Optional[str] = None, root_id: Optional[str] = None,
             rel_path: Optional[str] = None) -> Optional[dict]:
    with _st._connect(db_path) as conn:
        if file_id:
            return _row(conn.execute("SELECT * FROM workspace_files WHERE file_id = ?", (file_id,)).fetchone())
        if root_id and rel_path is not None:
            return _row(_file_by(conn, root_id, ci(_norm_rel(rel_path))))
    return None


def _project_prefix_clause(project: str, db_path: Path) -> tuple[str, list]:
    folders = list_folders(db_path, project=project)
    if not folders:
        return "0", []
    parts, params = [], []
    for f in folders:
        if f["rel_path_ci"] == "":
            # bound at the workspace root: every file under that root belongs to the project
            parts.append("(wf.root_id = ?)")
            params += [f["root_id"]]
            continue
        parts.append("(wf.root_id = ? AND (wf.rel_path_ci = ? OR substr(wf.rel_path_ci, 1, ?) = ?))")
        params += [f["root_id"], f["rel_path_ci"], len(f["rel_path_ci"]) + 1, f["rel_path_ci"] + "/"]
    return "(" + " OR ".join(parts) + ")", params


_SORTS = {"ref_degree": "wf.ref_degree DESC, wf.mtime DESC",
          "recent": "wf.mtime DESC", "path": "wf.rel_path ASC"}


def list_files(project: str, db_path: Path, folder: Optional[str] = None, ext: Optional[str] = None,
               sort: str = "ref_degree", limit: int = 50) -> list[dict]:
    clause, params = _project_prefix_clause(project, db_path)
    sql = f"SELECT wf.* FROM workspace_files wf WHERE wf.status = 'active' AND {clause}"
    if folder:
        fci = ci(_norm_rel(folder))
        sql += " AND (wf.rel_path_ci = ? OR substr(wf.rel_path_ci, 1, ?) = ?)"
        params += [fci, len(fci) + 1, fci + "/"]
    if ext:
        sql += " AND wf.ext = ?"
        params.append(ext.lower().lstrip("."))
    sql += " ORDER BY " + _SORTS.get(sort, _SORTS["ref_degree"]) + " LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    with _st._connect(db_path) as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for r in rows:
        r["project"] = project
    return rows


def count_files(project: str, db_path: Path, folder: Optional[str] = None) -> int:
    clause, params = _project_prefix_clause(project, db_path)
    sql = f"SELECT COUNT(*) FROM workspace_files wf WHERE wf.status = 'active' AND {clause}"
    if folder:
        fci = ci(_norm_rel(folder))
        sql += " AND (wf.rel_path_ci = ? OR substr(wf.rel_path_ci, 1, ?) = ?)"
        params += [fci, len(fci) + 1, fci + "/"]
    with _st._connect(db_path) as conn:
        return int(conn.execute(sql, params).fetchone()[0])


def find_files(query: str, db_path: Path, project: Optional[str] = None, limit: int = 10) -> list[dict]:
    q = ci(query.strip())
    if not q:
        return []
    like = f"%{_like_escape(q)}%"
    with _st._connect(db_path) as conn:
        rows = conn.execute(
            """SELECT *,
                      CASE WHEN basename_ci = ? THEN 4
                           WHEN basename_ci LIKE ? ESCAPE '\\' THEN 3
                           WHEN lower(title) LIKE ? ESCAPE '\\' THEN 2
                           WHEN rel_path_ci LIKE ? ESCAPE '\\' THEN 1 ELSE 0 END AS score
               FROM workspace_files
               WHERE status = 'active'
                 AND (basename_ci LIKE ? ESCAPE '\\' OR lower(title) LIKE ? ESCAPE '\\' OR rel_path_ci LIKE ? ESCAPE '\\')
               ORDER BY score DESC, ref_degree DESC, rel_path_ci LIMIT 200""",
            (q, like, like, like, like, like, like)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        owner = owner_project(d["root_id"], d["rel_path"], db_path)
        d["project"] = owner["project"] if owner else ""
        if project and d["project"] != project:
            continue
        out.append(d)
        if len(out) >= limit:
            break
    return out


def update_ref_degrees(file_ids: set[str], db_path: Path) -> None:
    if not file_ids:
        return
    with _st._connect(db_path) as conn:
        for fid in file_ids:
            deg = conn.execute(
                """SELECT COALESCE(SUM(fl.weight * (m.importance / 5.0)), 0)
                   FROM file_links fl JOIN memories m ON m.id = fl.src_id
                   WHERE fl.dst_kind = 'file' AND fl.dst_id = ? AND fl.kind = 'file_ref'
                     AND fl.src_kind = 'memory' AND m.status = 'active'""", (fid,)).fetchone()[0]
            conn.execute("UPDATE workspace_files SET ref_degree = ? WHERE file_id = ?", (round(deg, 4), fid))
        conn.commit()


def file_context(file_id: str, db_path: Path) -> dict:
    f = get_file(db_path, file_id=file_id)
    if f is None:
        return {"error": "file not found", "file_id": file_id}
    owner = owner_project(f["root_id"], f["rel_path"], db_path)
    with _st._connect(db_path) as conn:
        mems = conn.execute(
            """SELECT m.id, m.summary, m.type, m.importance, m.timestamp, fl.weight, fl.meta
               FROM file_links fl JOIN memories m ON m.id = fl.src_id
               WHERE fl.dst_kind = 'file' AND fl.dst_id = ? AND fl.kind = 'file_ref'
                 AND fl.src_kind = 'memory' AND m.status = 'active'
               ORDER BY m.importance DESC, m.timestamp DESC LIMIT 40""", (file_id,)).fetchall()
        links_out = conn.execute(
            """SELECT wf.file_id, wf.rel_path FROM file_links fl
               JOIN workspace_files wf ON wf.file_id = fl.dst_id
               WHERE fl.src_kind = 'file' AND fl.src_id = ? AND fl.kind = 'links_to'""", (file_id,)).fetchall()
        links_in = conn.execute(
            """SELECT wf.file_id, wf.rel_path FROM file_links fl
               JOIN workspace_files wf ON wf.file_id = fl.src_id
               WHERE fl.src_kind = 'file' AND fl.dst_id = ? AND fl.kind = 'links_to'""", (file_id,)).fetchall()
        touched = conn.execute(
            """SELECT m.id, m.summary, m.timestamp FROM file_links fl JOIN memories m ON m.id = fl.src_id
               WHERE fl.dst_kind = 'file' AND fl.dst_id = ? AND fl.kind = 'touched'
               ORDER BY m.timestamp DESC LIMIT 5""", (file_id,)).fetchall()
    memories = []
    for r in mems:
        d = dict(r)
        try:
            d["how"] = json.loads(d.pop("meta") or "{}").get("how", "")
        except ValueError:
            d["how"] = ""
        memories.append(d)
    return {"file": f, "project": owner["project"] if owner else "",
            "memories": memories, "links_in": [dict(r) for r in links_in],
            "links_out": [dict(r) for r in links_out], "touched_by": [dict(r) for r in touched],
            "dangling_tokens": []}


def workspace_map(db_path: Path, project: Optional[str] = None) -> dict:
    roots = list_roots(db_path)
    folders = list_folders(db_path)
    projects = _st.list_projects(db_path=db_path)
    if project:
        projects = [p for p in projects if p.slug == project]
    out_projects = []
    with _st._connect(db_path) as conn:
        for p in projects:
            homes = [f for f in folders if f["project"] == p.slug]
            clause, params = _project_prefix_clause(p.slug, db_path)
            n = conn.execute(
                f"SELECT COUNT(*) FROM workspace_files wf WHERE wf.status = 'active' AND {clause}",
                params).fetchone()[0]
            out_projects.append({
                "slug": p.slug, "name": p.name, "description": p.description,
                "description_source": p.description_source,
                "last_activity": p.last_activity.isoformat(), "file_count": n,
                "home_folders": [{"root_id": f["root_id"], "rel_path": f["rel_path"], "role": f["role"],
                                  "label": f["label"], "how": f["how"], "confidence": f["confidence"],
                                  "confirmed": f["confirmed"]} for f in homes]})
    return {
        "roots": roots,
        "projects": out_projects,
        "folders": folders if not project else [f for f in folders if f["project"] == project],
        "unmapped_folders": [f for f in folders if f["project"] == ""],
        "inferred_folders": [f for f in folders if f["how"] == "memory" and not f["confirmed"]],
        "last_scan": {r["machine"]: r["last_seen"] for r in roots},
    }
