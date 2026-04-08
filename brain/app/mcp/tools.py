import json
from typing import Optional
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import mcp.types as types

from ..storage import get_memory, get_recent, list_projects as storage_list_projects, DB_PATH
from ..search import hybrid_search
from ..ingest_pipeline import ingest
from ..models import MemoryEntry
from ..ingestion.plugins import ACTIVE_PLUGINS, INACTIVE_PLUGINS

server = Server("memorybrain")


# ── Tool handler functions (testable independently of MCP protocol) ──────────

async def handle_search_memory(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
) -> str:
    results = await hybrid_search(query, limit=limit, project=project, type_filter=type_filter, days=days)
    return json.dumps(results, default=str)


async def handle_get_memory(memory_id: str) -> str:
    entry = get_memory(memory_id, db_path=DB_PATH)
    if entry is None:
        return json.dumps({"error": f"Memory {memory_id} not found"})
    return json.dumps({
        "id": entry.id, "content": entry.content, "summary": entry.summary,
        "type": entry.type, "project": entry.project, "tags": entry.tags,
        "source": entry.source, "importance": entry.importance,
        "timestamp": entry.timestamp.isoformat(),
    })


async def handle_add_memory(
    content: str,
    type: str,
    project: str,
    tags: Optional[list] = None,
    source: str = "",
) -> str:
    entry = MemoryEntry(content=content, type=type, project=project, tags=tags or [], source=source)
    result = await ingest(entry)
    return json.dumps({"id": result.id, "summary": result.summary, "importance": result.importance})


async def handle_get_recent_context(project: Optional[str] = None, days: int = 7) -> str:
    rows = get_recent(project=project, days=days, db_path=DB_PATH)
    return json.dumps(rows, default=str)


async def handle_list_projects() -> str:
    projects = storage_list_projects(db_path=DB_PATH)
    lines = []

    # Plugin status header
    active_names = [p.MEMORY_TYPE for p in ACTIVE_PLUGINS]
    inactive_names = [p.MEMORY_TYPE for p in INACTIVE_PLUGINS]
    if active_names or inactive_names:
        active_str = "  ".join(f"{n} ✅" for n in active_names) if active_names else "none"
        inactive_str = "  ".join(f"{n} ❌" for n in inactive_names) if inactive_names else ""
        lines.append(f"Active plugins:   {active_str}")
        if inactive_str:
            lines.append(f"Inactive plugins: {inactive_str}")
        lines.append("")

    lines.append("## Projects\n")
    for p in projects:
        lines.append(f"**{p.slug}** — {p.name}")
        if p.one_liner:
            lines.append(f"  {p.one_liner}")
        lines.append(f"  Last activity: {p.last_activity.strftime('%Y-%m-%d')}")
        lines.append("")
    return "\n".join(lines)


async def handle_get_startup_summary() -> str:
    projects = storage_list_projects(db_path=DB_PATH)
    if not projects:
        return "No projects recorded yet."
    lines = ["# MemoryBrain — Session Context\n"]
    for p in projects[:10]:  # cap at 10 to stay under token budget
        line = f"- **{p.slug}**: {p.one_liner or p.name} (last: {p.last_activity.strftime('%Y-%m-%d')})"
        lines.append(line)
    return "\n".join(lines)


# ── MCP Server wiring ────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_memory",
            description="Hybrid keyword+semantic search across all memories. Returns summaries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "project": {"type": "string"},
                    "type_filter": {"type": "string"},
                    "days": {"type": "integer"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_memory",
            description="Fetch full content of a specific memory by ID.",
            inputSchema={
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="add_memory",
            description="Store a new memory entry. Summarised and indexed automatically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "type": {"type": "string", "enum": ["note", "fact", "session", "handover", "file"]},
                    "project": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string"},
                },
                "required": ["content", "type", "project"],
            },
        ),
        types.Tool(
            name="get_recent_context",
            description="Return the most recent memory entries chronologically.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "days": {"type": "integer", "default": 7},
                },
            },
        ),
        types.Tool(
            name="list_projects",
            description="List all known projects with status and last activity.",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="get_startup_summary",
            description="Compact project index suitable for session startup injection.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    handlers = {
        "search_memory": lambda a: handle_search_memory(**a),
        "get_memory": lambda a: handle_get_memory(**a),
        "add_memory": lambda a: handle_add_memory(**a),
        "get_recent_context": lambda a: handle_get_recent_context(**a),
        "list_projects": lambda _: handle_list_projects(),
        "get_startup_summary": lambda _: handle_get_startup_summary(),
    }
    if name not in handlers:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    result = await handlers[name](arguments)
    return [types.TextContent(type="text", text=result)]
