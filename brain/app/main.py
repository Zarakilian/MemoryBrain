import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .storage import init_db, list_projects, get_next_session_notes, DB_PATH
from .auth import require_api_key
from .mcp_discovery import read_mcp_tools

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


@app.get("/status")
async def status():
    return {
        "version": "0.4.0",
        "project_count": len(list_projects(db_path=DB_PATH)),
    }


@app.get("/mcp-tools")
async def mcp_tools():
    return read_mcp_tools()


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
