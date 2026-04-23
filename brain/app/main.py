import logging
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
from .summarise import _get_ollama_client, _get_embed_model, _get_summarise_model

# Module-level references — initialised eagerly so that tests can patch
# 'app.main.ollama_client' and have the /readiness handler see the mock.
# When a non-Ollama provider is active, ollama_client will be None and the
# /readiness handler skips the Ollama-specific checks.
ollama_client = _get_ollama_client()
EMBED_MODEL = _get_embed_model()
SUMMARISE_MODEL = _get_summarise_model()
from .chroma import get_client as get_chroma_client, COLLECTION_NAME

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Brain started")
    yield


app = FastAPI(title="MemoryBrain", version="0.5.0", lifespan=lifespan)
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

    if request.url.path in public_paths:
        return await call_next(request)

    try:
        await require_api_key(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Claude Code's MCP client parses 404 bodies with a Zod schema expecting an `error` field.
    # FastAPI's default 404 body is {"detail":"Not Found"} which fails that schema and leaves
    # the client stuck in "auth required" mode. Return OAuth-style error bodies so probes of
    # /.well-known/* cleanly signal "no OAuth here" and the client proceeds unauthenticated.
    if exc.status_code == 404:
        return JSONResponse(
            status_code=404,
            content={"error": "not_found", "error_description": "Not found"},
        )
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})


app.include_router(session_router)
app.include_router(manual_router)


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


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/readiness")
async def readiness():
    """Full subsystem check. Always public (no auth required).

    Returns ready=true only when all subsystems (SQLite, ChromaDB, Ollama,
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

    # ChromaDB
    try:
        chroma = get_chroma_client()
        chroma.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
        checks["chromadb"] = "ok"
    except Exception:
        checks["chromadb"] = "error"

    # Ollama + model presence (only checked when using OllamaProvider).
    # `ollama_client`, `EMBED_MODEL`, `SUMMARISE_MODEL` are module-level names
    # so that tests can patch them via `patch("app.main.ollama_client")`.
    # `global` here tells Python we want the module namespace lookup, not a local.
    global ollama_client, EMBED_MODEL, SUMMARISE_MODEL  # noqa: PLW0603
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

    ready = all(v == "ok" for v in checks.values())
    return {"ready": ready, "checks": checks}


@app.get("/status")
async def status():
    return {
        "version": "0.5.0",
        "project_count": len(list_projects(db_path=DB_PATH)),
    }


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


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
