import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .storage import init_db, list_projects, get_next_session_note, DB_PATH
from .db import connect
from .auth import require_api_key
from .models import ValidationError
from .security import HostCheckMiddleware, WriteGuardMiddleware, mcp_transport_security
from .summarise import (_get_ollama_client, _get_embed_model, _get_summarise_model,
                        _get_provider, provider_warning)

# Module-level references — initialised eagerly so that tests can patch
# 'app.main.ollama_client' and have the /readiness handler see the mock.
# When a non-Ollama provider is active, ollama_client will be None and the
# /readiness handler skips the Ollama-specific checks.
try:
    ollama_client = _get_ollama_client()
    EMBED_MODEL = _get_embed_model()
    SUMMARISE_MODEL = _get_summarise_model()
except Exception as _provider_error:  # a misconfigured provider must not stop the brain
    logging.getLogger(__name__).error("AI provider could not start: %s", _provider_error)
    ollama_client, EMBED_MODEL, SUMMARISE_MODEL = None, "", ""
from .vector import get_backend, vec_ready, startup_backfill
from .reembed import pending_count, reembed_batch, reembed_loop

logger = logging.getLogger(__name__)

# Loopback-only MCP + health surfaces. Bound to 127.0.0.1 in compose; no
# remote network exposure. Classic SSE clients use /sse + /messages/; Grok and
# other streamable-HTTP clients use /mcp.
MCP_PUBLIC_PATHS = {
    "/sse",
    "/messages/",
    "/mcp",
    "/health",
    "/readiness",
}
MCP_PUBLIC_PREFIXES = ("/mcp/", "/messages", "/ui", "/api/ui", "/static")

# Single process-wide streamable HTTP manager (required by the MCP SDK).
# stateless=True: each request is independent — ideal for local single-user tools.
# DNS-rebinding protection allows the same Host names as the app: loopback
# plus MEMORYBRAIN_ALLOWED_HOSTS.
_streamable_security = mcp_transport_security()
streamable_session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    json_response=False,
    stateless=True,
    security_settings=_streamable_security,
)


def read_version() -> str:
    """The release in the VERSION file: /app/VERSION in the image, the repo
    root in a checkout. "unknown" when neither exists."""
    here = Path(__file__).resolve()
    for candidate in (here.parents[1] / "VERSION", here.parents[2] / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text
    return "unknown"


APP_VERSION = read_version()


def _reembed_rate() -> int:
    """MEMORYBRAIN_REEMBED_RATE: memories re-embedded per minute (0 disables)."""
    raw = os.getenv("MEMORYBRAIN_REEMBED_RATE", "25")
    try:
        return max(0, int(raw))
    except ValueError:
        logger.warning("MEMORYBRAIN_REEMBED_RATE=%r is not a number, using 25", raw)
        return 25


def _reembed_pending():
    try:
        return pending_count(db_path=DB_PATH)
    except Exception:
        logger.exception("could not count memories waiting for a re-embed")
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    init_db()
    # v2.0.0: idempotent one-time copy of embeddings out of the legacy Chroma
    # directory into brain.db (no-op once complete, no-op on chroma backend).
    report = startup_backfill()
    if not report.get("skipped") and report.get("missing_before"):
        logger.info(f"Vector backfill report: {report}")
    logger.info(f"Brain started (vector backend: {get_backend()})")
    # v2.3: optional nightly light auto-sleep
    stop_sched = asyncio.Event()
    sched_task = None
    try:
        from .scheduler import scheduler_loop, auto_consolidate_enabled
        if auto_consolidate_enabled():
            sched_task = asyncio.create_task(
                scheduler_loop(stop_sched), name="auto-consolidate"
            )
            logger.info("auto-consolidate scheduler task created")
    except Exception:
        logger.exception("failed to start auto-consolidate scheduler")
    # v3: throttled re-embed of vectors from older models (2.x vectors have model '')
    stop_reembed = asyncio.Event()
    reembed_task = None
    rate = _reembed_rate() if get_backend() == "sqlite_vec" else 0
    if rate > 0:
        reembed_task = asyncio.create_task(reembed_loop(stop_reembed, rate), name="reembed")
        logger.info("re-embed task created (%d per minute)", rate)
    # StreamableHTTPSessionManager.run() owns the request task group for /mcp.
    async with streamable_session_manager.run():
        logger.info("Streamable HTTP MCP session manager started at /mcp")
        yield
    stop_sched.set()
    stop_reembed.set()
    for task in (sched_task, reembed_task):
        if task is None:
            continue
        try:
            await asyncio.wait_for(task, timeout=5)
        except Exception:
            task.cancel()
    logger.info("Streamable HTTP MCP session manager stopped")


class PureASGIAuthMiddleware:
    """API-key gate that does not wrap the response body.

    FastAPI/Starlette `@app.middleware("http")` uses BaseHTTPMiddleware, which
    buffers/rewrites the response stream and breaks Server-Sent Events used by
    classic MCP SSE (`GET /sse`) and can corrupt streamable HTTP. This pure
    ASGI middleware only inspects the request and then passes through the raw
    ASGI app — safe for long-lived streaming transports.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "") or ""
        # Public MCP/health/UI surfaces (loopback trust boundary).
        if path in MCP_PUBLIC_PATHS or any(path.startswith(p) for p in MCP_PUBLIC_PREFIXES):
            # UI write endpoints still require a key when configured.
            if not path.startswith("/api/ui/edit"):
                await self.app(scope, receive, send)
                return

        api_key = os.getenv("BRAIN_API_KEY")
        if not api_key:
            await self.app(scope, receive, send)
            return

        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        presented = headers.get("x-brain-key", "")
        if presented != api_key:
            body = b'{"detail":"Invalid or missing API key"}'
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


app = FastAPI(title="MemoryBrain", version=APP_VERSION, lifespan=lifespan)
# Classic SSE gets the same DNS-rebinding protection as /mcp (CVE-2025-66416).
sse_transport = SseServerTransport("/messages/", security_settings=_streamable_security)

# Pure ASGI middleware only (BaseHTTPMiddleware breaks SSE). The last one added
# runs first: Host check, then the keyless write guard, then the API key.
app.add_middleware(PureASGIAuthMiddleware)
app.add_middleware(WriteGuardMiddleware)
app.add_middleware(HostCheckMiddleware)


@app.exception_handler(ValidationError)
async def validation_error_handler(request: Request, exc: ValidationError):
    """Bad input is the caller's problem: 422 with the reason, never a 500."""
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Return OAuth-formatted error bodies for 404s.

    Claude Code's MCP client (v0.2+) probes /.well-known/oauth-protected-resource
    and other OAuth discovery endpoints before connecting to SSE servers. FastAPI's
    default 404 body {"detail":"Not Found"} fails the client's Zod schema, which
    expects an "error" field. This leaves Claude Code stuck in "needs authentication"
    mode, exposing only a meta-authenticate tool instead of the real MCP tools.

    By returning {"error": "not_found", "error_description": "Not found"} on 404,
    the client's schema validation passes, it concludes "no OAuth here", and
    proceeds with the unauthenticated SSE connection.
    """
    if exc.status_code == 404:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "error_description": "Not found"},
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})


app.include_router(session_router)
app.include_router(manual_router)

from .workspace.routes import router as workspace_router  # v2.5 workspace layer
app.include_router(workspace_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/readiness")
async def readiness():
    """Full subsystem check. Always public (no auth required).

    Returns ready=true only when all subsystems (SQLite, vector store, provider,
    both models) are operational. Used by the session hook at startup to report
    degraded service with actionable fix instructions.
    """
    checks: dict[str, str] = {}

    # SQLite
    try:
        conn = connect(DB_PATH, readonly=True)
        conn.execute("SELECT 1")
        conn.close()
        checks["sqlite"] = "ok"
    except Exception:
        checks["sqlite"] = "error"

    # Vector store (sqlite_vec by default; chroma when rolled back).
    # Key kept as "vector_store"; "chromadb" mirrored for older hooks/scripts.
    checks["vector_store"] = "ok" if vec_ready() else "error"
    checks["chromadb"] = checks["vector_store"]

    # Provider-specific checks — Ollama or Gemini or OpenAI
    global ollama_client, EMBED_MODEL, SUMMARISE_MODEL  # noqa: PLW0603

    # Determine which provider is active
    try:
        active_provider = _get_provider().__class__.__name__
    except Exception as exc:
        active_provider = "unavailable"
        checks["provider"] = f"error: {exc}"[:120]

    if active_provider == "OllamaProvider":
        # Ollama + model presence checks
        if ollama_client is not None:
            try:
                response = await ollama_client.list()
                model_names = [
                    (m.model if hasattr(m, "model") else m.get("model", m.get("name", "")))
                    for m in (response.models if hasattr(response, "models") else response.get("models", []))
                ]
                checks["ollama"] = "ok"
                checks["embedding_model"] = "ok" if any(EMBED_MODEL in n for n in model_names) else "missing"
                checks["summary_model"] = "ok" if any(SUMMARISE_MODEL in n for n in model_names) else "missing"
            except Exception:
                checks["ollama"] = "error"
                checks["embedding_model"] = "unknown"
                checks["summary_model"] = "unknown"
        else:
            checks["ollama"] = "skipped"
            checks["embedding_model"] = "skipped"
            checks["summary_model"] = "skipped"

    elif active_provider == "GeminiProvider":
        # Gemini provider checks
        if not os.getenv("GOOGLE_API_KEY"):
            checks["gemini_api_key"] = "missing"
        else:
            try:
                from .summarise import GeminiProvider
                provider = GeminiProvider()
                # Try a lightweight test call
                test_embedding = await provider.embed("test")
                checks["gemini_api_key"] = "ok"
                checks["gemini_client"] = "ok" if isinstance(test_embedding, list) else "error"
            except Exception as e:
                checks["gemini_api_key"] = "ok"  # Key exists but client failed
                checks["gemini_client"] = f"error: {str(e)[:50]}"

    elif active_provider == "OpenAIProvider":
        # OpenAI provider checks
        if not os.getenv("OPENAI_API_KEY"):
            checks["openai_api_key"] = "missing"
        else:
            checks["openai_api_key"] = "ok"

    ready = all(v == "ok" for v in checks.values())
    return {"ready": ready, "checks": checks,
            "reembed_pending": _reembed_pending(), "provider_warning": provider_warning()}


@app.get("/status")
async def status():
    """Runtime matrix for multi-AI adapters (Grok/Claude/Codex/Gemini)."""
    from .mcp.tools import CORE_TOOLS, TOOL_NAMES, tool_profile
    from pathlib import Path as _P
    stamp = ""
    stamp_path = _P(__file__).parent / "BUILD_STAMP"
    if stamp_path.exists():
        try:
            stamp = stamp_path.read_text(encoding="utf-8").strip()
        except Exception:
            stamp = ""
    from .scheduler import scheduler_status
    return {
        "version": APP_VERSION,
        "project_count": len(list_projects(db_path=DB_PATH)),
        "build_stamp": stamp,
        "scheduler": scheduler_status(db_path=DB_PATH),
        "reembed_pending": _reembed_pending(),
        "mcp": {
            "sse": "/sse",
            "sse_messages": "/messages/",
            "streamable_http": "/mcp",
            "stdio": "docker exec -i memorybrain-brain-1 python stdio_server.py",
            "tool_count": len(TOOL_NAMES),
            "tools": TOOL_NAMES,
            "profile": tool_profile(),  # MEMORYBRAIN_TOOLS: core (default) or full
            "core_tools": list(CORE_TOOLS),
            "recommended": {
                "grok": {"transport": "streamable_http", "url": "http://localhost:7741/mcp"},
                "claude": {"transport": "sse", "url": "http://localhost:7741/sse"},
                "codex": {
                    "transport": "stdio",
                    "command": "docker",
                    "args": ["exec", "-i", "memorybrain-brain-1", "python", "stdio_server.py"],
                },
                "gemini": {
                    "transport": "stdio",
                    "command": "docker",
                    "args": ["exec", "-i", "memorybrain-brain-1", "python", "/app/stdio_server.py"],
                },
            },
        },
    }


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


@app.get("/project-brief")
async def project_brief_endpoint(
    project: str,
    intent: str = "",
    max_chars: int = 3500,
    include_system: bool = True,
    days: int = 14,
):
    """REST twin of get_project_brief for non-MCP clients."""
    from .brief import build_project_brief
    if not project:
        raise HTTPException(422, "project is required")
    return await build_project_brief(
        project=project,
        intent=intent or None,
        max_chars=max_chars,
        include_system=include_system,
        days=days,
        db_path=DB_PATH,
    )


@app.get("/conflicts")
async def conflicts_endpoint(project: str = "", limit: int = 50):
    from .conflicts import list_conflicts
    return list_conflicts(project=project or None, limit=limit, db_path=DB_PATH)


@app.post("/admin/backfill-vectors")
async def backfill_vectors():
    """Re-embed any memories missing a vector (after Chroma backfill gaps).
    Authenticated via the standard API-key middleware; loopback-only."""
    startup_report = startup_backfill()
    return {"backfill": startup_report,
            **(await reembed_batch(500, db_path=DB_PATH, force=True))}


@app.get("/next-session")
async def next_session(project: str = ""):
    """The newest active next_session note of THIS project, with who wrote it
    and when. No project means no note: never another project's plan."""
    note = get_next_session_note(project, db_path=DB_PATH)
    if not note:
        return {"notes": "", "id": None, "writer": None, "timestamp": None}
    return {"notes": note["content"], "id": note["id"], "writer": note["writer"],
            "timestamp": note["timestamp"]}


@app.get("/sse")
async def sse_endpoint(request: Request):
    """Classic MCP SSE transport (GET open stream; posts go to /messages/)."""
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


@app.post("/messages/")
async def handle_messages(request: Request):
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)


async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI entry for MCP streamable HTTP (Grok --transport http)."""
    await streamable_session_manager.handle_request(scope, receive, send)


# Mount streamable HTTP under /mcp so Grok can use:
#   url = "http://localhost:7741/mcp"  with transport http
# The mount strips the prefix; the session manager receives the remaining path.
from starlette.routing import Mount

app.router.routes.insert(
    0,
    Mount("/mcp", app=handle_streamable_http),
)


@app.post("/admin/rebuild-graph")
async def rebuild_graph_endpoint():
    """Recompute the derived graph edges (semantic, tag, reference,
    session_chain). Belief citations and conflict verdicts, dismissed ones
    included, are kept. Authenticated via the standard API-key middleware."""
    from .linker import rebuild_graph
    return rebuild_graph()


@app.post("/admin/consolidate")
async def consolidate_endpoint(project: str = "", idle_days: int = 14,
                               mode: str = "full"):
    """Run one consolidation cycle (the brain's sleep): distil clusters into
    beliefs, damp their sources, flag contradictions, extract open loops,
    decay the unrecalled. Additive and derived — never deletes. Optionally
    scoped to one project. mode=full|light (light skips LLM beliefs).
    Authenticated via the API-key middleware."""
    from .consolidate import consolidate
    return await consolidate(project=project or None,
                             idle_days=max(1, min(idle_days, 365)),
                             mode=mode or "full")


@app.post("/admin/auto-consolidate")
async def auto_consolidate_endpoint(force: bool = True):
    """Trigger the auto-sleep cycle now (same as the nightly scheduler)."""
    from .scheduler import run_auto_consolidate
    return await run_auto_consolidate(force=force)


@app.post("/admin/export/obsidian")
async def export_obsidian(project: str, include_archived: bool = False):
    """Export a project to Markdown suitable for opening as an Obsidian vault.
    Files land under /app/data/exports/<project>/."""
    from pathlib import Path as _P
    from .obsidian import export_project_markdown
    if not project:
        raise HTTPException(422, "project is required")
    out = _P("/app/data/exports")
    return export_project_markdown(
        project, out, include_archived=include_archived, db_path=DB_PATH,
    )


@app.post("/admin/import/obsidian")
async def import_obsidian(directory: str, project: str = ""):
    """Import Markdown files from a directory into MemoryBrain."""
    from pathlib import Path as _P
    from .obsidian import import_markdown_dir
    if not directory:
        raise HTTPException(422, "directory is required")
    try:
        return await import_markdown_dir(
            _P(directory), project=project or None, db_path=DB_PATH,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))


# ── Synapse — Agent Exchange (v2.4) — REST twins of the MCP tools ───────────
from pydantic import BaseModel as _BaseModel
from typing import Optional as _Optional


class ThreadCreateRequest(_BaseModel):
    project: str
    title: str
    body: str
    from_agent: str
    to_agent: str = ""
    kind: str = "task"
    priority: int = 0
    refs: list[str] = []


class ThreadReplyRequest(_BaseModel):
    body: str
    from_agent: str
    to_agent: str = ""
    intent: str = "update"
    refs: list[str] = []
    status: _Optional[str] = None


class ThreadStatusRequest(_BaseModel):
    status: str
    agent: str = ""


@app.post("/exchange/threads", status_code=201)
async def exchange_create_thread(req: ThreadCreateRequest):
    from .exchange import post_task
    try:
        return post_task(project=req.project, title=req.title, body=req.body,
                         from_agent=req.from_agent, to_agent=req.to_agent,
                         kind=req.kind, priority=req.priority, refs=req.refs,
                         db_path=DB_PATH)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/exchange/threads")
async def exchange_list_threads(project: str = "", status: str = "",
                                agent: str = "", limit: int = 50):
    from .exchange import list_threads
    try:
        return list_threads(project=project or None, status=status or None,
                            agent=agent or None, limit=limit, db_path=DB_PATH)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/exchange/threads/{thread_id}")
async def exchange_get_thread(thread_id: str):
    from .exchange import get_thread
    result = get_thread(thread_id, db_path=DB_PATH)
    if result is None:
        raise HTTPException(404, "Thread not found")
    return result


@app.post("/exchange/threads/{thread_id}/messages", status_code=201)
async def exchange_reply(thread_id: str, req: ThreadReplyRequest):
    from .exchange import reply_to_thread
    try:
        result = reply_to_thread(thread_id=thread_id, body=req.body,
                                 from_agent=req.from_agent, to_agent=req.to_agent,
                                 intent=req.intent, refs=req.refs,
                                 status=req.status, db_path=DB_PATH)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.post("/exchange/threads/{thread_id}/status")
async def exchange_status(thread_id: str, req: ThreadStatusRequest):
    from .exchange import update_task_status
    try:
        result = update_task_status(thread_id, req.status, agent=req.agent,
                                    db_path=DB_PATH)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/exchange/inbox")
async def exchange_inbox(agent: str, project: str = "",
                         include_broadcast: bool = True,
                         mark_read: bool = False, limit: int = 20):
    """Read-only by default (a GET must not change state); pass
    mark_read=true to advance the read cursor."""
    from .exchange import get_inbox
    try:
        return get_inbox(agent=agent, project=project or None,
                         include_broadcast=include_broadcast,
                         mark_read=mark_read, limit=limit, db_path=DB_PATH)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/search")
async def search_endpoint(q: str, project: str = "", type: str = "", limit: int = 10,
                          as_of: str = ""):
    """REST twin of MCP search_memory: the same result list, but read-only. A GET
    never changes state, so it records no retrieval event and boosts no recall
    (a cross-site <img> must not steer ranking). X-Took-Ms carries the latency;
    X-Degraded is set when semantic search was unavailable (keyword results only)."""
    import json as _json
    import time as _time
    from fastapi.responses import Response as _Response
    from .mcp.tools import handle_search_memory
    started = _time.perf_counter()
    reply = _json.loads(await handle_search_memory(
        q, limit=max(1, min(int(limit), 100)), project=project or None,
        type_filter=type or None, source="rest-search", as_of=as_of or None, record=False))
    headers = {"X-Took-Ms": f"{(_time.perf_counter() - started) * 1000:.1f}"}
    if isinstance(reply, dict):  # degraded: {"results", "degraded"}
        headers["X-Degraded"] = reply.get("degraded", "")
        reply = reply.get("results", [])
    return _Response(content=_json.dumps(reply, default=str), media_type="application/json",
                     headers=headers)


@app.get("/admin/retrieval-log")
async def retrieval_log(limit: int = 500):
    """Distinct (query, project) pairs from past searches, with chosen ids as
    relevant: the starting point for a hand-labelled eval set (brain eval)."""
    from .retrieval import query_log
    return {"queries": query_log(limit=limit, db_path=DB_PATH)}


@app.get("/timeline")
async def timeline_endpoint(project: str = "", days: int = 30, limit: int = 100):
    from .timeline import get_timeline
    return get_timeline(project=project or None, days=days, limit=limit,
                        db_path=DB_PATH)


@app.get("/entities")
async def entities_endpoint(project: str = "", limit: int = 40):
    from .timeline import get_entities
    return get_entities(project=project or None, limit=limit, db_path=DB_PATH)


# ── Web UI (v2.0.0) — local, read-only, server-rendered ─────────────────────
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles
from .ui import ui_router
from .ui.editor import router as ui_editor_router

app.mount("/static", StaticFiles(directory=str(_Path(__file__).resolve().parent / "static")), name="static")
app.include_router(ui_router)
app.include_router(ui_editor_router)
