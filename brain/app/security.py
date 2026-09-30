"""HTTP guards for a service that only ever talks to its own machine.

HostCheckMiddleware refuses any request whose Host is not a loopback name
(421). That stops DNS rebinding: a page that points evil.example at
127.0.0.1 still sends Host: evil.example.

WriteGuardMiddleware stops drive-by writes when no API key is set. A web
page can only send a JSON body or a custom header after a CORS preflight,
and this app answers no preflight, so a state-changing request must carry
Content-Type application/json or an X-Brain-Client header. With a key set,
the API-key middleware decides instead.

Both are pure ASGI: BaseHTTPMiddleware buffers responses and breaks SSE.
"""
from __future__ import annotations

import json
import os

from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WRITE_GUARD_DETAIL = "write needs Content-Type application/json or an X-Brain-Client header"


def host_name(header: str) -> str:
    """The host part of a Host header, lowercased: 'LocalHost:7741' ->
    'localhost', '[::1]:7741' -> '[::1]'."""
    host = (header or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        rest = host[end + 1:] if end != -1 else ""
        if end != -1 and (rest == "" or (rest[:1] == ":" and rest[1:].isdigit())):
            return host[:end + 1]
        return host  # junk after the bracket: never a known name
    if host.count(":") == 1:  # name or IPv4 with a port
        return host.split(":", 1)[0]
    return host


def _bracketed(host: str) -> str:
    """A bare IPv6 address as a Host header carries it: 'fe80::1' -> '[fe80::1]'."""
    host = host.strip().lower()
    return f"[{host}]" if host.count(":") > 1 and not host.startswith("[") else host


def allowed_hosts() -> frozenset[str]:
    """Loopback names plus MEMORYBRAIN_ALLOWED_HOSTS (comma separated)."""
    extra = os.getenv("MEMORYBRAIN_ALLOWED_HOSTS", "")
    return LOOPBACK_HOSTS | {host_name(_bracketed(h)) for h in extra.split(",") if h.strip()}


def mcp_transport_security() -> TransportSecuritySettings:
    """The MCP SDK's own Host and Origin check, fed the same names as the app's,
    so an extra host reaches /sse, /messages/ and /mcp as well as REST."""
    hosts = sorted(allowed_hosts())
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h for host in hosts for h in (host, f"{host}:*")],
        allowed_origins=[o for host in hosts for scheme in ("http", "https")
                         for o in (f"{scheme}://{host}", f"{scheme}://{host}:*")],
    )


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])}


async def _reply(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode("ascii"))]})
    await send({"type": "http.response.body", "body": body})


def _mcp_sdk_path(path: str) -> bool:
    """The MCP SDK validates these requests itself (Host, Origin, content type)."""
    return path == "/mcp" or path.startswith("/mcp/") or path.startswith("/messages/")


class HostCheckMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            if host_name(_headers(scope).get("host", "")) not in allowed_hosts():
                await _reply(send, 421, "MemoryBrain answers only on localhost "
                                        "(add the name to MEMORYBRAIN_ALLOWED_HOSTS)")
                return
        await self.app(scope, receive, send)


class WriteGuardMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] == "http" and scope.get("method") in STATE_CHANGING
                and not _mcp_sdk_path(scope.get("path", ""))
                and not os.getenv("BRAIN_API_KEY")):
            headers = _headers(scope)
            ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if ctype != "application/json" and "x-brain-client" not in headers:
                await _reply(send, 403, WRITE_GUARD_DETAIL)
                return
        await self.app(scope, receive, send)
