import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings

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
# DNS-rebinding protection allows only loopback Host headers.
_streamable_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=[
        "localhost",
        "localhost:*",
        "127.0.0.1",
        "127.0.0.1:*",
        "[::1]",
        "[::1]:*",
    ],
    allowed_origins=[
        "http://localhost",
        "http://localhost:*",
        "http://127.0.0.1",
        "http://127.0.0.1:*",
        "http://[::1]",
        "http://[::1]:*",
    ],
)
streamable_session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    json_response=False,
    stateless=True,
    security_settings=_streamable_security,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # v2.0.0: idempotent one-time copy of embeddings out of the legacy Chroma
    # directory into brain.db (no-op once complete, no-op on chroma backend).
    report = startup_backfill()
    if not report.get("skipped") and report.get("missing_before"):
        logger.info(f"Vector backfill report: {report}")
    logger.info(f"Brain started (vector backend: {get_backend()})")
    # StreamableHTTPSessionManager.run() owns the request task group for /mcp.
    async with streamable_session_manager.run():
        logger.info("Streamable HTTP MCP session manager started at /mcp")
        yield
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


app = FastAPI(title="MemoryBrain", version="2.2.0", lifespan=lifespan)
sse_transport = SseServerTransport("/messages/")

# Pure ASGI middleware first so it wraps the whole stack without body buffering.
app.add_middleware(PureASGIAuthMiddleware)


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
    """Runtime matrix for multi-AI adapters (Grok/Claude/Codex/Gemini)."""
    from .mcp.tools import TOOL_NAMES
    from pathlib import Path as _P
    stamp = ""
    stamp_path = _P(__file__).parent / "BUILD_STAMP"
    if stamp_path.exists():
        try:
            stamp = stamp_path.read_text(encoding="utf-8").strip()
        except Exception:
            stamp = ""
    return {
        "version": "2.2.0",
        "project_count": len(list_projects(db_path=DB_PATH)),
        "build_stamp": stamp,
        "mcp": {
            "sse": "/sse",
            "sse_messages": "/messages/",
            "streamable_http": "/mcp",
            "stdio": "docker exec -i memorybrain-brain-1 python stdio_server.py",
            "tool_count": len(TOOL_NAMES),
            "tools": TOOL_NAMES,
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
    reembed_report = await reembed_missing()
    return {"backfill": startup_report, **reembed_report}


@app.get("/next-session")
async def next_session(project: str = ""):
    notes = get_next_session_notes(project, db_path=DB_PATH)
    return {"notes": notes}


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
    """Drop and recompute all memory-graph edges. Safe any time — edges are
    derived data. Authenticated via the standard API-key middleware."""
    from .linker import rebuild_graph
    return rebuild_graph()


@app.post("/admin/consolidate")
async def consolidate_endpoint(project: str = "", idle_days: int = 14):
    """Run one consolidation cycle (the brain's sleep): distil clusters into
    beliefs, damp their sources, flag contradictions, extract open loops,
    decay the unrecalled. Additive and derived — never deletes. Optionally
    scoped to one project. Authenticated via the API-key middleware."""
    from .consolidate import consolidate
    return await consolidate(project=project or None,
                             idle_days=max(1, min(idle_days, 365)))


# ── Web UI (v2.0.0) — local, read-only, server-rendered ─────────────────────
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles
from .ui import ui_router
from .ui.editor import router as ui_editor_router

app.mount("/static", StaticFiles(directory=str(_Path(__file__).resolve().parent / "static")), name="static")
app.include_router(ui_router)
app.include_router(ui_editor_router)
