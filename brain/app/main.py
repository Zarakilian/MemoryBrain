import logging
import sqlite3
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .storage import init_db, list_projects, get_next_session_notes, DB_PATH
from .auth import require_api_key
from .summarise import _client as ollama_client, EMBED_MODEL, SUMMARISE_MODEL
from .chroma import get_client as get_chroma_client, COLLECTION_NAME

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Brain started")
    yield


app = FastAPI(title="MemoryBrain", version="0.4.0", lifespan=lifespan)
sse_transport = SseServerTransport("/messages/")


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    try:
        await require_api_key(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return await call_next(request)


app.include_router(session_router)
app.include_router(manual_router)


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

    # Ollama + model presence
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

    ready = all(v == "ok" for v in checks.values())
    return {"ready": ready, "checks": checks}


@app.get("/status")
async def status():
    return {
        "version": "0.4.0",
        "project_count": len(list_projects(db_path=DB_PATH)),
    }


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


@app.get("/next-session")
async def next_session(project: str = ""):
    if not project:
        return {"notes": ""}
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
