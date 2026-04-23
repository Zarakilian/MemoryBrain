import json
from typing import Optional
from mcp.server import Server
import mcp.types as types

from ..storage import get_memory, get_recent, list_projects as storage_list_projects, delete_memory, get_project_recent_state, DB_PATH
from ..search import hybrid_search
from ..ingest_pipeline import ingest
from ..models import MemoryEntry
from ..chroma import chroma_delete

server = Server("memorybrain")


async def handle_search_memory(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
) -> str:
    results = await hybrid_search(
        query, limit=limit, project=project, type_filter=type_filter,
        days=days, tags=tags, include_history=include_history,
    )
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
        "status": entry.status, "superseded_by": entry.superseded_by,
        "supersedes": entry.supersedes,
    })


async def handle_add_memory(
    content: str,
    type: str,
    project: str,
    tags: Optional[list] = None,
    source: str = "",
    description: str = "",
) -> str:
    entry = MemoryEntry(content=content, type=type, project=project,
                        tags=tags or [], source=source)
    if description:
        entry.summary = description  # bypass LLM summariser
    result = await ingest(entry)
    return json.dumps({
        "id": result.id,
        "summary": result.summary,
        "importance": result.importance,
        "superseded": result.superseded,
        "potential_supersessions": result.potential_supersessions,
    })


async def handle_delete_memory(memory_id: str) -> str:
    entry = get_memory(memory_id, db_path=DB_PATH)
    if entry is None:
        return json.dumps({"error": f"Memory {memory_id} not found"})
    delete_memory(memory_id, db_path=DB_PATH)
    chroma_delete(memory_id)
    return json.dumps({"deleted": True, "id": memory_id})


async def handle_get_recent_context(project: Optional[str] = None, days: int = 7) -> str:
    rows = get_recent(project=project, days=days, db_path=DB_PATH)
    return json.dumps(rows, default=str)


async def handle_list_projects() -> str:
    projects = storage_list_projects(db_path=DB_PATH)
    lines = ["## Projects\n"]
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
    lines = ["# MemoryBrain — Session Context\n", "## Projects"]
    for p in projects[:5]:
        recent_state = get_project_recent_state(p.slug, db_path=DB_PATH)
        line = f"- **{p.slug}** (last: {p.last_activity.strftime('%Y-%m-%d')})"
        if recent_state:
            line += f": {recent_state}"
        lines.append(line)

    recent = get_recent(days=7, limit=5, db_path=DB_PATH)
    if recent:
        lines.append("\n## Recent Memories (last 7 days)")
        for r in recent:
            preview = (r.get("summary") or r.get("content_preview") or "")[:200]
            lines.append(f"- [{r['project']}] {preview}")

    return "\n".join(lines)


# ── MCP Server wiring ─────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_memory",
            description="Hybrid keyword+semantic search. Returns summaries. Active memories only by default.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "project": {"type": "string"},
                    "type_filter": {"type": "string", "enum": ["note", "fact", "session", "handover", "file", "reference"]},
                    "days": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "include_history": {"type": "boolean", "default": False, "description": "Include archived (superseded) memories"},
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
            description="Store a new memory. Auto-detects and archives superseded memories. Pass description to skip LLM summariser.",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "type": {"type": "string", "enum": ["note", "fact", "session", "handover", "file", "reference"]},
                    "project": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string"},
                    "description": {"type": "string", "description": "If provided, used as summary directly — bypasses LLM summariser"},
                },
                "required": ["content", "type", "project"],
            },
        ),
        types.Tool(
            name="delete_memory",
            description="Hard delete a memory by ID. Use for wrong entries only — use supersession for stale ones.",
            inputSchema={
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
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
            description="Compact project index with per-project recent state — use at session start.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


def _validate_and_extract(arguments: dict, required: list[str], optional: list[str]) -> dict:
    missing = [k for k in required if k not in arguments]
    if missing:
        raise ValueError(f"Missing required argument(s): {', '.join(missing)}")
    allowed = set(required) | set(optional)
    return {k: arguments[k] for k in arguments if k in allowed}


def _clamp_int(value, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(int(value), hi))
    except (TypeError, ValueError):
        return default


_TOOL_ARGS = {
    "search_memory":       (["query"], ["limit", "project", "type_filter", "days", "tags", "include_history"]),
    "get_memory":          (["memory_id"], []),
    "add_memory":          (["content", "type", "project"], ["tags", "source", "description"]),
    "delete_memory":       (["memory_id"], []),
    "get_recent_context":  ([], ["project", "days"]),
    "list_projects":       ([], []),
    "get_startup_summary": ([], []),
}


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name not in _TOOL_ARGS:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]
    required, optional = _TOOL_ARGS[name]
    try:
        clean = _validate_and_extract(arguments, required, optional)
    except ValueError as e:
        return [types.TextContent(type="text", text=json.dumps({"error": str(e)}))]

    if "limit" in clean:
        clean["limit"] = _clamp_int(clean["limit"], 1, 100, 10)
    if "days" in clean:
        clean["days"] = _clamp_int(clean["days"], 1, 365, 7)

    handlers = {
        "search_memory":       lambda a: handle_search_memory(**a),
        "get_memory":          lambda a: handle_get_memory(**a),
        "add_memory":          lambda a: handle_add_memory(**a),
        "delete_memory":       lambda a: handle_delete_memory(**a),
        "get_recent_context":  lambda a: handle_get_recent_context(**a),
        "list_projects":       lambda _: handle_list_projects(),
        "get_startup_summary": lambda _: handle_get_startup_summary(),
    }
    result = await handlers[name](clean)
    return [types.TextContent(type="text", text=result)]
