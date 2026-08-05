# brain/app/ui/routes.py
"""MemoryBrain Atlas — UI pages + read-only JSON endpoints.

No writes anywhere in this module (connections are PRAGMA query_only).
Page structure: one continuous workspace at /ui (left rail, three lenses on
the same data, right inspector). Stream and the memory page are fully
server-rendered and usable with JS disabled; the palette, Constellation and
Chronicle lenses are progressive enhancements.

The /api/ui/{stats,search,graph} and /api/ui/memories/{id}/related response
shapes are shared with the MCP assistant tools' underlying queries and must
not change. New endpoints (version, stream, chronicle, memory detail) are
additive.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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

# Deterministic project colour: same hash + hue table lives in atlas.js
# (projectHue) — keep the two in sync. Muted hues tuned for the dark surface.
_HUES = (28, 82, 140, 190, 215, 255, 285, 320, 350, 45, 165, 5)


def _project_hue(slug) -> int:
    h = 0
    for ch in str(slug or ""):
        h = (h * 31 + ord(ch)) % 997
    return _HUES[h % 12]


templates.env.filters["phue"] = _project_hue

# HTML pages and the version endpoint must never be cached: the build stamp
# comparison on /ui/doctor relies on both being live. Static assets are the
# opposite — they carry the stamp in their URL, so caching them is safe.
_NO_STORE = {"Cache-Control": "no-store, max-age=0"}

VALID_LENSES = ("stream", "constellation", "chronicle")


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
def atlas(request: Request,
          project: str | None = None,
          type: str | None = None,
          min_importance: int = Query(1, ge=1, le=5),
          before: str | None = None,
          lens: str = "stream",
          conn: sqlite3.Connection = Depends(db)):
    """The Atlas shell. Stream is server-rendered inside it; Constellation
    and Chronicle activate client-side on the same data."""
    projects = q.all_projects(conn)
    proj = next((p for p in projects if p["slug"] == project), None)
    if project and proj is None:
        raise HTTPException(404, f"Unknown project: {project}")
    return templates.TemplateResponse(request, "index.html", {
        "stats": q.stats(conn),
        "stream": q.stream(conn, project=project, mtype=type,
                           min_importance=min_importance, before=before),
        "projects": projects,
        "current": proj,
        "filters": {"project": project or "", "type": type or "",
                    "min_importance": min_importance},
        "lens": lens if lens in VALID_LENSES else "stream",
        "types": q.VALID_TYPES,
    }, headers=_NO_STORE)


@router.get("/ui/memory/{memory_id}", response_class=HTMLResponse)
def memory_page(request: Request, memory_id: str,
                conn: sqlite3.Connection = Depends(db)):
    """Full memory page: deep link + no-JS fallback for the inspector."""
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
        "stats": q.stats(conn),
        "current": next((p for p in q.all_projects(conn)
                         if p["slug"] == mem.get("project")), None),
    }, headers=_NO_STORE)


@router.get("/ui/search", response_class=HTMLResponse)
async def search_page(request: Request, q_: str = Query("", alias="q"),
                      project: str | None = None, type: str | None = None,
                      conn: sqlite3.Connection = Depends(db)):
    """Server-rendered search results: the no-JS fallback for the palette."""
    results, mode = (await _search(conn, q_, project, type)) if q_ else ([], "none")
    return templates.TemplateResponse(request, "search.html", {
        "q": q_, "results": results, "search_mode": mode,
        "projects": q.all_projects(conn),
        "stats": q.stats(conn),
        "current": None,
        "filters": {"project": project or "", "type": type or ""},
    }, headers=_NO_STORE)


# Old bookmarks from the previous UI keep working.

@router.get("/ui/graph")
def legacy_graph(project: str | None = None):
    dest = "/ui?lens=constellation"
    if project:
        dest += f"&project={project}"
    return RedirectResponse(dest, status_code=301)


@router.get("/ui/project/{slug}")
def legacy_project(slug: str):
    return RedirectResponse(f"/ui?project={slug}", status_code=301)


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


# Additive endpoints for the Atlas lenses (new in the Atlas rebuild).

@router.get("/api/ui/stream")
def api_stream(project: str | None = None, type: str | None = None,
               min_importance: int = Query(1, ge=1, le=5),
               before: str | None = None,
               limit: int = Query(60, ge=1, le=200),
               conn: sqlite3.Connection = Depends(db)):
    """Same query that server-renders the Stream; used for load-more."""
    return q.stream(conn, project=project, mtype=type,
                    min_importance=min_importance, before=before, limit=limit)


@router.get("/api/ui/chronicle")
def api_chronicle(project: str | None = None,
                  limit: int = Query(500, ge=10, le=2000),
                  conn: sqlite3.Connection = Depends(db)):
    """Sessions/handovers per project + session_chain spine."""
    return q.chronicle(conn, project=project, limit=limit)


@router.get("/api/ui/memories/{memory_id}")
def api_memory(memory_id: str, conn: sqlite3.Connection = Depends(db)):
    """Full memory detail for the inspector."""
    mem = q.get_memory_row(conn, memory_id)
    if mem is None:
        raise HTTPException(404, "Memory not found")
    return mem


@router.get("/api/ui/conflicts")
def api_conflicts(project: str | None = None,
                  limit: int = Query(50, ge=1, le=200),
                  conn: sqlite3.Connection = Depends(db)):
    """Unresolved contradiction pairs flagged by the consolidation cycle.
    Additive endpoint (v2.1.0); read-only like all /api/ui reads."""
    return q.conflicts(conn, project=project, limit=limit)


@router.get("/api/ui/timeline")
def api_timeline(project: str | None = None,
                 days: int = Query(30, ge=1, le=365),
                 limit: int = Query(100, ge=1, le=500)):
    """Chronological activity feed (v2.3)."""
    from ..timeline import get_timeline
    return get_timeline(project=project, days=days, limit=limit)


@router.get("/api/ui/entities")
def api_entities(project: str | None = None,
                 limit: int = Query(40, ge=1, le=100)):
    """Entity cards: tags, names, services (v2.3)."""
    from ..timeline import get_entities
    return get_entities(project=project, limit=limit)


# ------------------------------------------------- Synapse / agents (v2.4)

@router.get("/ui/agents", response_class=HTMLResponse)
def agents_page(request: Request, project: str | None = None,
                days: int = Query(90, ge=1, le=365),
                conn: sqlite3.Connection = Depends(db)):
    """Synapse page: multi-AI analytics + the agent interaction network."""
    projects = q.all_projects(conn)
    proj = next((p for p in projects if p["slug"] == project), None)
    if project and proj is None:
        raise HTTPException(404, f"Unknown project: {project}")
    return templates.TemplateResponse(request, "agents.html", {
        "stats": q.stats(conn),
        "projects": projects,
        "current": proj,
        "days": days,
        "agents_page": True,
    }, headers=_NO_STORE)


@router.get("/api/ui/agents/stats")
def api_agent_stats(project: str | None = None,
                    days: int = Query(90, ge=1, le=365)):
    """Per-agent usage totals and per-project shares (read-only)."""
    from ..exchange import agent_stats
    return agent_stats(project=project or None, days=days)


@router.get("/api/ui/agents/network")
def api_agent_network(project: str | None = None,
                      days: int = Query(90, ge=1, le=365)):
    """Agent interaction graph for the Synapse view (read-only)."""
    from ..exchange import agent_network
    return agent_network(project=project or None, days=days)


@router.get("/api/ui/agents/threads")
def api_agent_threads(project: str | None = None, status: str | None = None,
                      limit: int = Query(50, ge=1, le=200)):
    """Recent collaboration threads for the Synapse page (read-only)."""
    from ..exchange import list_threads
    return list_threads(project=project or None, status=status or None,
                        limit=limit)
