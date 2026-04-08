"""Optional API key authentication for MemoryBrain.

Set BRAIN_API_KEY in .env to enable. If unset, auth is disabled (backward compat).
/health is always public.
"""
import os
from fastapi import Request, HTTPException

_API_KEY: str | None = os.getenv("BRAIN_API_KEY")

# Paths that never require auth
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json"}


async def require_api_key(request: Request):
    """FastAPI middleware: check X-Brain-Key header if BRAIN_API_KEY is set."""
    if _API_KEY is None:
        return  # auth disabled

    if request.url.path in PUBLIC_PATHS:
        return  # always public

    # SSE/MCP paths don't use HTTP auth (MCP has its own transport)
    if request.url.path.startswith("/sse") or request.url.path.startswith("/messages"):
        return

    key = request.headers.get("X-Brain-Key", "")
    if key != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
