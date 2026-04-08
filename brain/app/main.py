import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router
from .ingestion.plugins import discover_plugins, ACTIVE_PLUGINS, INACTIVE_PLUGINS
from .ingestion.scheduler import start_scheduler
from .storage import init_db, list_projects, DB_PATH

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    active, inactive = await discover_plugins()
    scheduler = start_scheduler(active)
    logger.info(f"Brain started — {len(active)} plugins active, {len(inactive)} inactive")
    yield
    scheduler.shutdown()


app = FastAPI(title="MemoryBrain", version="0.2.0", lifespan=lifespan)
sse_transport = SseServerTransport("/messages/")

app.include_router(session_router)
app.include_router(manual_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status")
async def status():
    return {
        "project_count": len(list_projects(db_path=DB_PATH)),
        "active_plugins": [p.MEMORY_TYPE for p in ACTIVE_PLUGINS],
        "inactive_plugins": [p.MEMORY_TYPE for p in INACTIVE_PLUGINS],
    }


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


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
