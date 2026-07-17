import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .storage import init_db, list_projects, get_next_session_notes, DB_PATH
from .auth import require_api_key
from .summarise import _get_ollama_client, _get_embed_model, _get_summarise_model, _get_provider

# Module-level references — initialised eagerly so that tests can patch
# 'app.main.ollama_client' and have the /readiness handler see the mock.
# When a non-Ollama provider is active, ollama_client will be None and the
# /readiness handler skips the Ollama-specific checks.
ollama_client = _get_ollama_client()
EMBED_MODEL = _get_embed_model()
SUMMARISE_MODEL = _get_summarise_model()
from .vector import get_backend, vec_ready, startup_backfill, reembed_missing

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # v2.0.0: idempotent one-time copy of embeddings out of the legacy Chroma
    # directory into brain.db (no-op once complete, no-op on chroma backend).
    report = startup_backfill()
    if not report.get("skipped") and report.get("missing_before"):
        logger.info(f"Vector backfill report: {report}")
    logger.info(f"Brain started (vector backend: {get_backend()})")
    yield


app = FastAPI(title="MemoryBrain", version="2.0.0", lifespan=lifespan)
sse_transport = SseServerTransport("/messages/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Skip auth for public endpoints needed for MCP transport and health checks
    public_paths = {
        "/sse",         # SSE connection
        "/messages/",   # SSE message post handler
        "/health",      # Server health check
        "/readiness",   # Server readiness check
    }

    # Web UI + static assets + read-only UI JSON: same trust boundary as /sse
    # (loopback-only binding is the auth; these paths cannot write).
    # /api/ui/edit/* is the deliberate exception: UI writes are enforced
    # exactly like /ingest/* — X-Brain-Key required whenever a key is set.
    ui_prefixes = ("/ui", "/api/ui", "/static")
    if (not request.url.path.startswith("/api/ui/edit")
            and (request.url.path in public_paths
                 or request.url.path.startswith(ui_prefixes))):
        return await call_next(request)

    try:
        await require_api_key(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


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
        conn = sqlite3.connect(str(DB_PATH))
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
    active_provider = _get_provider().__class__.__name__

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
    return {"ready": ready, "checks": checks}


@app.get("/status")
async def status():
    return {
        "version": "2.0.0",
        "project_count": len(list_projects(db_path=DB_PATH)),
    }


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


@app.post("/admin/backfill-vectors")
async def backfill_vectors():
    """Re-embed any memories missing a vector (after Chroma backfill gaps).
    Authenticated via the standard API-key middleware; loopback-only."""
    startup_report = startup_backfill()
    reembed_report = await reembed_missing()
    return {"backfill": startup_report, **reembed_report}


@app.get("/next-session")
async def next_session(project: str = ""):
    notes = get_next_session_notes(project, db_path=DB_PATH)
    return {"notes": notes}


@app.get("/sse")
async def sse_endpoint(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


@app.post("/messages/")
async def handle_messages(request: Request):
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)


@app.post("/admin/rebuild-graph")
async def rebuild_graph_endpoint():
    """Drop and recompute all memory-graph edges. Safe any time — edges are
    derived data. Authenticated via the standard API-key middleware."""
    from .linker import rebuild_graph
    return rebuild_graph()


# ── Web UI (v2.0.0) — local, read-only, server-rendered ─────────────────────
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles
from .ui import ui_router
from .ui.editor import router as ui_editor_router

app.mount("/static", StaticFiles(directory=str(_Path(__file__).resolve().parent / "static")), name="static")
app.include_router(ui_router)
app.include_router(ui_editor_router)
