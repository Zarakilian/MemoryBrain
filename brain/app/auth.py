"""Optional API key authentication for MemoryBrain.

Set BRAIN_API_KEY in .env to enable. If unset, auth is disabled (backward compat).
/health is always public.
"""
import os
from fastapi import Request, HTTPException

_API_KEY: str | None = os.getenv("BRAIN_API_KEY")

# Paths that never require auth
PUBLIC_PATHS = {
    "/health",
    "/readiness",
    "/docs",
    "/openapi.json",
    "/sse",
    "/messages/",
    "/mcp",
}


async def require_api_key(request: Request):
    """FastAPI dependency/helper: check X-Brain-Key header if BRAIN_API_KEY is set."""
    if _API_KEY is None:
        return  # auth disabled

    if request.url.path in PUBLIC_PATHS:
        return  # always public

    # Classic SSE + streamable HTTP MCP transports are loopback-public
    # (compose binds 127.0.0.1 only). UI edit + ingest remain keyed.
    path = request.url.path
    if (
        path.startswith("/sse")
        or path.startswith("/messages")
        or path == "/mcp"
        or path.startswith("/mcp/")
    ):
        return

    key = request.headers.get("X-Brain-Key", "")
    if key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
