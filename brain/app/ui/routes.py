# brain/app/ui/routes.py
"""UI pages + read-only JSON endpoints. No writes anywhere in this module.

Search uses the same hybrid pipeline as the search_memory MCP tool and
falls back to keyword-only FTS5 when the embedding provider is down.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..graph_queries import get_graph, get_related
from ..search import hybrid_search
from . import queries as q
from .build_info import get_build_stamp
from .doctor import DOCTOR_HTML

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ui"])
_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Available in every template: footer stamp + cache-busting asset URLs
# (?b={{ build_stamp }}). Fixed for the container's lifetime.
templates.env.globals["build_stamp"] = get_build_stamp()


def db():
    conn = q.get_conn()
    try:
        yield conn
    finally:
        conn.close()


async def _search(conn, text: str, project: str | None, mtype: str | None,
                  limit: int = 30) -> tuple[list, str]:
    """Hybrid search with keyword-only fallback. Returns (results, mode)."""
    try:
        results = await hybrid_search(text, limit=limit, project=project,
                                      type_filter=mtype)
        return results, "hybrid"
    except Exception:
        logger.warning("Hybrid search unavailable — falling back to keyword-only")
        return q.search_fts(conn, text, project=project, mtype=mtype,
                            limit=limit), "keyword"


# HTML pages and the version endpoint must never be cached: the build stamp
# comparison on /ui/doctor relies on both being live. Static assets are the
# opposite — they carry the stamp in their URL, so caching them is safe.
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}


# ----------------------------------------------------------- diagnostics

@router.get("/api/ui/version")
def api_version():
    """Build stamp baked at container build time (brain/Dockerfile).
    First question in every debugging exchange: what build does this say?"""
    return JSONResponse({"build": get_build_stamp()}, headers=_NO_STORE)


@router.get("/ui/doctor", response_class=HTMLResponse)
def doctor_page():
    """Dependency-free in-browser diagnostics. Deliberately takes no DB
    connection and references no static assets: if FastAPI is up, this
    page renders, whatever else is broken."""
    return HTMLResponse(DOCTOR_HTML.replace("__BUILD__", get_build_stamp()),
                        headers=_NO_STORE)


# ------------------------------------------------------------------- pages

@router.get("/ui", response_class=HTMLResponse)
def dashboard(request: Request, conn: sqlite3.Connection = Depends(db)):
    return templates.TemplateResponse(request, "dashboard.html", {
        "stats": q.stats(conn),
        "recent": q.recent_memories(conn, limit=12),
        "top": q.top_by_degree(conn, limit=8),
        "projects": q.all_projects(conn),
        "active": "dashboard",
    })


@router.get("/ui/project/{slug}", response_class=HTMLResponse)
def project_page(request: Request, slug: str,
                 type: str | None = None,
                 min_importance: int = Query(1, ge=1, le=5),
                 sort: str = "recent",
                 conn: sqlite3.Connection = Depends(db)):
    projects = q.all_projects(conn)
    proj = next((p for p in projects if p["slug"] == slug), None)
    if proj is None:
        raise HTTPException(404, f"Unknown project: {slug}")
    return templates.TemplateResponse(request, "project.html", {
        "project": proj,
        "memories": q.project_memories(conn, slug, mtype=type,
                                       min_importance=min_importance, sort=sort),
        "projects": projects,
        "filters": {"type": type or "", "min_importance": min_importance,
                    "sort": sort},
        "active": slug,
    })


@router.get("/ui/memory/{memory_id}", response_class=HTMLResponse)
def memory_page(request: Request, memory_id: str,
                conn: sqlite3.Connection = Depends(db)):
    mem = q.get_memory_row(conn, memory_id)
    if mem is None:
        raise HTTPException(404, "Memory not found")
    related = get_related(memory_id, limit=20, min_weight=0.2,
                          include_archived=True)
    rel = related["related"] if related else []
    backlinks = [r for r in rel
                 if any(e.get("direction") == "in" for e in r["explanations"])]
    return templates.TemplateResponse(request, "memory.html", {
        "m": mem, "related": rel, "backlinks": backlinks,
        "projects": q.all_projects(conn),
        "active": mem.get("project"),
    })


@router.get("/ui/graph", response_class=HTMLResponse)
def graph_page(request: Request, project: str | None = None,
               conn: sqlite3.Connection = Depends(db)):
    return templates.TemplateResponse(request, "graph.html", {
        "project": project or "",
        "projects": q.all_projects(conn),
        "active": "graph",
    })


@router.get("/ui/search", response_class=HTMLResponse)
async def search_page(request: Request, q_: str = Query("", alias="q"),
                      project: str | None = None, type: str | None = None,
                      conn: sqlite3.Connection = Depends(db)):
    results, mode = (await _search(conn, q_, project, type)) if q_ else ([], "none")
    return templates.TemplateResponse(request, "search.html", {
        "q": q_, "results": results, "search_mode": mode,
        "projects": q.all_projects(conn),
        "filters": {"project": project or "", "type": type or ""},
        "active": "search",
    })


# --------------------------------------------------------- JSON (read-only)

@router.get("/api/ui/stats")
def api_stats(conn: sqlite3.Connection = Depends(db)):
    return q.stats(conn)


@router.get("/api/ui/search")
async def api_search(q_: str = Query(..., alias="q", min_length=1),
                     project: str | None = None, type: str | None = None,
                     limit: int = Query(10, ge=1, le=50),
                     conn: sqlite3.Connection = Depends(db)):
    results, mode = await _search(conn, q_, project, type, limit=limit)
    return {"results": results, "mode": mode}


@router.get("/api/ui/graph")
def api_graph(project: str | None = None,
              min_weight: float = Query(0.35, ge=0.0, le=1.0),
              max_nodes: int = Query(150, ge=10, le=500),
              include_archived: bool = False):
    return get_graph(project=project or None, min_weight=min_weight,
                     max_nodes=max_nodes, include_archived=include_archived)


@router.get("/api/ui/memories/{memory_id}/related")
def api_related(memory_id: str,
                min_weight: float = Query(0.2, ge=0.0, le=1.0),
                limit: int = Query(20, ge=1, le=50)):
    result = get_related(memory_id, limit=limit, min_weight=min_weight)
    if result is None:
        raise HTTPException(404, "Memory not found")
    return result
