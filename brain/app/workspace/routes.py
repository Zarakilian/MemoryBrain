"""REST surface for the workspace layer. Loopback only, behind the API key middleware."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import storage as _st
from . import store as ws
from .identity import backfill_descriptions, header_line, set_identity
from .resolve import rebuild_file_links

router = APIRouter()


class ManifestFile(BaseModel):
    rel_path: str
    size: int = 0
    mtime: str = ""
    sha256: str = ""
    title: str = ""


class Marker(BaseModel):
    rel_path: str
    project: str


class ScanRequest(BaseModel):
    machine: str
    root_id: str
    abs_path: str
    scanned_at: str = ""
    full: bool = False
    files: list[ManifestFile] = []
    markers: list[Marker] = []


class FolderRow(BaseModel):
    rel_path: str
    project: str = ""
    role: str = "primary"
    label: str = ""
    remote_url: str = ""
    confirmed: bool = False


class MapRequest(BaseModel):
    root_id: str
    folders: list[FolderRow]


class BindRequest(BaseModel):
    project: str
    how: str = "cwd"
    root_id: str = ""
    rel_path: str = ""
    abs_path: str = ""
    label: str = ""
    role: str = "primary"
    confidence: Optional[float] = None


class ProjectInfoRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    home_path: Optional[str] = None
    label: str = ""
    role: str = "primary"
    source: str = "tool"


@router.post("/workspace/scan")
async def workspace_scan(req: ScanRequest):
    report = ws.apply_manifest(req.model_dump(), db_path=_st.DB_PATH)
    if report["added"] or report["moved"]:
        report["relink"] = rebuild_file_links(db_path=_st.DB_PATH)
    return report


@router.post("/workspace/map")
async def workspace_map_write(req: MapRequest):
    bound = seen = 0
    for f in req.folders:
        if f.project:
            try:
                r = ws.bind_folder(req.root_id, f.rel_path, f.project, "init", db_path=_st.DB_PATH,
                                   label=f.label, role=f.role, remote_url=f.remote_url,
                                   confirmed=1 if f.confirmed else 0)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
            bound += 1 if r["written"] else 0
        else:
            ws.record_folder_seen(req.root_id, f.rel_path, f.remote_url, db_path=_st.DB_PATH)
            seen += 1
    return {"bound": bound, "seen": seen}


BIND_SOURCES = ("cwd", "memory", "init")  # marker and tool bindings never come over REST


def _check_bind(req: BindRequest) -> None:
    if req.how not in BIND_SOURCES:
        raise HTTPException(status_code=422, detail=f"how must be one of {', '.join(BIND_SOURCES)}")
    rel = (req.rel_path or "").replace("\\", "/")
    if rel.startswith("/") or rel[1:2] == ":" or ".." in rel.split("/"):
        raise HTTPException(status_code=422,
                            detail="rel_path must be relative to the root, without '..'")


@router.post("/workspace/bind")
async def workspace_bind(req: BindRequest):
    _check_bind(req)
    if req.how == "cwd" and req.abs_path:
        p = req.abs_path.replace("\\", "/").strip()
        if not (p[1:2] == ":" or p.startswith("/")):
            # an unexpanded template such as {{cwd}} is not a path: never bind it
            return {"written": False, "reason": "cwd_path_not_absolute", "row": None, "conflict_with": ""}
    if req.abs_path:
        resolved = ws.resolve_abs_path(req.abs_path, db_path=_st.DB_PATH)
        if resolved is None:
            return {"written": False, "reason": "path_outside_known_roots", "row": None, "conflict_with": ""}
        root_id, rel = resolved
    else:
        root_id, rel = req.root_id, req.rel_path
    if not root_id:
        raise HTTPException(status_code=422, detail="root_id or abs_path is required")
    if req.how == "cwd":
        if not ws._norm_rel(rel):
            return {"written": False, "reason": "root_not_bound_from_cwd", "row": None, "conflict_with": ""}
        if req.confidence is not None:
            owner = ws.owner_project(root_id, rel, db_path=_st.DB_PATH)
            if owner is not None:
                return {"written": False, "reason": "inside_bound_folder", "row": owner, "conflict_with": ""}
    try:
        return ws.bind_folder(root_id, rel, req.project, req.how, db_path=_st.DB_PATH,
                              label=req.label, role=req.role, confidence=req.confidence)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/workspace/map")
async def workspace_map_read(project: str = ""):
    return ws.workspace_map(db_path=_st.DB_PATH, project=project or None)


@router.get("/workspace/files")
async def workspace_files(project: str, folder: str = "", ext: str = "",
                          sort: str = "ref_degree", limit: int = 50):
    files = ws.list_files(project, db_path=_st.DB_PATH, folder=folder or None,
                          ext=ext or None, sort=sort, limit=limit)
    return {"project": project, "count": len(files), "files": files}


@router.get("/workspace/find")
async def workspace_find(query: str, project: str = "", limit: int = 10):
    files = ws.find_files(query, db_path=_st.DB_PATH, project=project or None, limit=limit)
    return {"query": query, "count": len(files), "files": files}


@router.get("/workspace/file/{file_id}")
async def workspace_file(file_id: str):
    ctx = ws.file_context(file_id, db_path=_st.DB_PATH)
    if "error" in ctx:
        raise HTTPException(status_code=404, detail=ctx["error"])
    return ctx


@router.post("/projects/{slug}/info")
async def project_info(slug: str, req: ProjectInfoRequest):
    try:
        out = set_identity(slug, db_path=_st.DB_PATH, name=req.name, description=req.description,
                           source=req.source, home_path=req.home_path, label=req.label, role=req.role)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    out["header"] = header_line(out["identity"])
    return out


@router.post("/admin/rebuild-file-links")
async def admin_rebuild_file_links():
    return rebuild_file_links(db_path=_st.DB_PATH)


@router.post("/admin/backfill-project-descriptions")
async def admin_backfill_descriptions():
    return await backfill_descriptions(db_path=_st.DB_PATH)
