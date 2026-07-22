import json
from typing import Optional
from mcp.server import Server
import mcp.types as types

from ..storage import (get_memory, get_recent,
                       list_projects as storage_list_projects, delete_memory,
                       get_project_recent_state, record_recall,
                       RECALL_BOOST_SEARCH, DB_PATH, _connect)
from ..search import hybrid_search
from ..ingest_pipeline import ingest
from ..models import MemoryEntry
from ..vector import vec_delete

server = Server("memorybrain")

# Keep in sync with list_tools() and /status mcp.tools
TOOL_NAMES = [
    "search_memory",
    "get_memory",
    "add_memory",
    "delete_memory",
    "get_recent_context",
    "list_projects",
    "get_startup_summary",
    "get_related_memories",
    "get_memory_graph",
    "consolidate_memory",
    "get_project_brief",
    "list_conflicts",
    "resolve_conflict",
    "dismiss_conflict",
    "pin_memory",
    "unpin_memory",
    "list_pins",
]

MEMORY_TYPE_ENUM = [
    "note", "fact", "session", "handover", "file", "reference",
    "belief", "decision", "open_loop",
]


def _belief_sources(memory_id: str) -> list[dict]:
    with _connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT m.id, m.summary, substr(m.content, 1, 160) AS preview
               FROM memory_links l
               JOIN memories m ON m.id = l.dst_id
               WHERE l.src_id = ? AND l.kind = 'derived_from'
               LIMIT 12""",
            (memory_id,),
        ).fetchall()
    return [
        {"id": r["id"], "summary": r["summary"] or r["preview"] or ""}
        for r in rows
    ]


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
    try:
        record_recall([r["id"] for r in results],
                      boost=RECALL_BOOST_SEARCH, db_path=DB_PATH)
    except Exception:
        pass
    return json.dumps(results, default=str)


async def handle_get_memory(memory_id: str) -> str:
    entry = get_memory(memory_id, db_path=DB_PATH)
    if entry is None:
        return json.dumps({"error": f"Memory {memory_id} not found"})
    try:
        record_recall([memory_id], db_path=DB_PATH)
    except Exception:
        pass
    payload = {
        "id": entry.id, "content": entry.content, "summary": entry.summary,
        "type": entry.type, "project": entry.project, "tags": entry.tags,
        "source": entry.source, "importance": entry.importance,
        "timestamp": entry.timestamp.isoformat(),
        "status": entry.status, "superseded_by": entry.superseded_by,
        "supersedes": entry.supersedes,
    }
    if entry.type == "belief":
        payload["sources"] = _belief_sources(memory_id)
    return json.dumps(payload, default=str)


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
    try:
        result = await ingest(entry)
    except Exception as e:
        # Surface ValidationError cleanly to MCP clients
        return json.dumps({"error": str(e)})
    body = {
        "id": result.id,
        "summary": result.summary,
        "importance": result.importance,
        "superseded": result.superseded,
        "potential_supersessions": result.potential_supersessions,
    }
    # Only emit structured fields when they are real (tests may use MagicMock)
    if isinstance(getattr(result, "type", None), str):
        body["type"] = result.type
    if isinstance(getattr(result, "tags", None), list):
        body["tags"] = result.tags
    warnings = getattr(result, "_write_warnings", None)
    if isinstance(warnings, list) and warnings:
        body["warnings"] = warnings
    return json.dumps(body)


async def handle_delete_memory(memory_id: str) -> str:
    entry = get_memory(memory_id, db_path=DB_PATH)
    if entry is None:
        return json.dumps({"error": f"Memory {memory_id} not found"})
    delete_memory(memory_id, db_path=DB_PATH)
    vec_delete(memory_id, db_path=DB_PATH)
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
    from ..conflicts import list_conflicts
    from ..pins import list_pins

    projects = storage_list_projects(db_path=DB_PATH)
    if not projects:
        return "No projects recorded yet."
    lines = ["# MemoryBrain — Session Context\n", "## Projects"]
    for p in projects[:5]:
        recent_state = get_project_recent_state(p.slug, db_path=DB_PATH)
        line = f"- **{p.slug}** (last: {p.last_activity.strftime('%Y-%m-%d')})"
        if recent_state:
            line += f": {recent_state}"
        try:
            n_pins = len(list_pins(p.slug, db_path=DB_PATH))
            n_conf = list_conflicts(project=p.slug, limit=50, db_path=DB_PATH).get("total", 0)
            extras = []
            if n_pins:
                extras.append(f"{n_pins} pins")
            if n_conf:
                extras.append(f"{n_conf} conflicts")
            if extras:
                line += f" [{', '.join(extras)}]"
        except Exception:
            pass
        lines.append(line)

    recent = get_recent(days=7, limit=5, db_path=DB_PATH)
    if recent:
        lines.append("\n## Recent Memories (last 7 days)")
        for r in recent:
            preview = (r.get("summary") or r.get("content_preview") or "")[:200]
            lines.append(f"- [{r['project']}] {preview}")

    lines.append(
        "\n_Tip: call get_project_brief(project=…) for a token-budgeted pack "
        "with pins, beliefs, facts, open loops, and conflicts._"
    )
    return "\n".join(lines)


async def handle_get_related_memories(
    memory_id: str,
    limit: int = 10,
    min_weight: float = 0.3,
    kinds: Optional[list] = None,
    include_archived: bool = False,
) -> str:
    from ..graph_queries import get_related
    result = get_related(memory_id, limit=limit, min_weight=min_weight,
                         kinds=kinds, include_archived=include_archived,
                         db_path=DB_PATH)
    if result is None:
        return json.dumps({"error": f"Memory {memory_id} not found"})
    return json.dumps(result, default=str)


async def handle_consolidate_memory(project: Optional[str] = None,
                                    idle_days: int = 14) -> str:
    from ..consolidate import consolidate
    report = await consolidate(project=project or None, idle_days=idle_days)
    return json.dumps(report, default=str)


async def handle_get_memory_graph(
    project: Optional[str] = None,
    min_weight: float = 0.35,
    max_nodes: int = 150,
    include_archived: bool = False,
) -> str:
    from ..graph_queries import get_graph
    return json.dumps(get_graph(project=project, min_weight=min_weight,
                                max_nodes=max_nodes,
                                include_archived=include_archived,
                                db_path=DB_PATH), default=str)


async def handle_get_project_brief(
    project: str,
    intent: Optional[str] = None,
    max_chars: Optional[int] = None,
    include_system: Optional[bool] = None,
    days: int = 14,
) -> str:
    from ..brief import build_project_brief
    pack = await build_project_brief(
        project=project,
        intent=intent,
        max_chars=max_chars,
        include_system=include_system,
        days=days,
        db_path=DB_PATH,
    )
    return json.dumps(pack, default=str)


async def handle_list_conflicts(project: Optional[str] = None,
                                limit: int = 50) -> str:
    from ..conflicts import list_conflicts
    return json.dumps(list_conflicts(project=project, limit=limit, db_path=DB_PATH),
                      default=str)


async def handle_resolve_conflict(winner_id: str, loser_id: str) -> str:
    from ..conflicts import resolve_conflict
    return json.dumps(resolve_conflict(winner_id, loser_id, db_path=DB_PATH))


async def handle_dismiss_conflict(a_id: str, b_id: str) -> str:
    from ..conflicts import dismiss_conflict
    return json.dumps(dismiss_conflict(a_id, b_id, db_path=DB_PATH))


async def handle_pin_memory(
    project: str,
    memory_id: str,
    kind: str = "truth",
    label: str = "",
    priority: int = 0,
) -> str:
    from ..pins import pin_memory
    return json.dumps(pin_memory(
        project=project, memory_id=memory_id, kind=kind,
        label=label, priority=priority, db_path=DB_PATH,
    ))


async def handle_unpin_memory(project: str, memory_id: str) -> str:
    from ..pins import unpin_memory
    return json.dumps(unpin_memory(project, memory_id, db_path=DB_PATH))


async def handle_list_pins(project: str) -> str:
    from ..pins import list_pins
    return json.dumps({"project": project, "pins": list_pins(project, db_path=DB_PATH)},
                      default=str)


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
                    "type_filter": {"type": "string", "enum": MEMORY_TYPE_ENUM},
                    "days": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "include_history": {"type": "boolean", "default": False,
                                        "description": "Include archived (superseded) memories"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_memory",
            description="Fetch full content of a specific memory by ID. Beliefs include source citations.",
            inputSchema={
                "type": "object",
                "properties": {"memory_id": {"type": "string"}},
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="add_memory",
            description=(
                "Store a new memory. Prefer type=fact/decision for durable truths, "
                "open_loop for unfinished work, session for narrative. "
                "Auto-supersedes similar active memories. Pass description to skip LLM summariser."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "type": {"type": "string", "enum": MEMORY_TYPE_ENUM},
                    "project": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string"},
                    "description": {"type": "string",
                                    "description": "If provided, used as summary directly — bypasses LLM summariser"},
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
        types.Tool(
            name="get_related_memories",
            description="Memories linked to a given memory via the automatic graph, ranked by combined weight.",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                    "min_weight": {"type": "number", "default": 0.3},
                    "kinds": {"type": "array", "items": {"type": "string",
                              "enum": ["semantic", "tag", "reference", "session_chain",
                                       "entity", "derived_from", "conflicts_with"]}},
                    "include_archived": {"type": "boolean", "default": False},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="get_memory_graph",
            description="Node/edge graph of memories and their derived links, per project or global.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "min_weight": {"type": "number", "default": 0.35},
                    "max_nodes": {"type": "integer", "default": 150},
                    "include_archived": {"type": "boolean", "default": False},
                },
            },
        ),
        types.Tool(
            name="consolidate_memory",
            description=("Run one consolidation cycle (the brain's sleep): distil "
                         "clusters into cited beliefs, flag contradictions, extract "
                         "open loops, decay unrecalled memories. Pins are not decayed. "
                         "Additive — never deletes."),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "idle_days": {"type": "integer", "default": 14,
                                  "description": "Memories unrecalled this long decay a little"},
                },
            },
        ),
        types.Tool(
            name="get_project_brief",
            description=(
                "Token-budgeted context pack for one project: pins, beliefs (with "
                "citations), facts/decisions, open loops, conflicts, recent activity, "
                "optional system ops lane, and optional intent search hits. "
                "Call at session start after get_startup_summary."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "intent": {"type": "string",
                               "description": "Optional focus query for hybrid search hits"},
                    "max_chars": {"type": "integer", "default": 3500},
                    "include_system": {"type": "boolean",
                                       "description": "Inject system project ops truths"},
                    "days": {"type": "integer", "default": 14},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="list_conflicts",
            description="List active contradiction pairs (conflicts_with) for a project or globally.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        ),
        types.Tool(
            name="resolve_conflict",
            description="Resolve a contradiction: keep winner, archive loser (reversible supersession).",
            inputSchema={
                "type": "object",
                "properties": {
                    "winner_id": {"type": "string"},
                    "loser_id": {"type": "string"},
                },
                "required": ["winner_id", "loser_id"],
            },
        ),
        types.Tool(
            name="dismiss_conflict",
            description="Keep both sides of a contradiction; tombstone the conflict edge so it stays quiet.",
            inputSchema={
                "type": "object",
                "properties": {
                    "a_id": {"type": "string"},
                    "b_id": {"type": "string"},
                },
                "required": ["a_id", "b_id"],
            },
        ),
        types.Tool(
            name="pin_memory",
            description="Pin an active memory into the project working set (goal/truth/branch/constraint/open_loop/custom).",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "memory_id": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["goal", "truth", "branch", "constraint", "open_loop", "custom"],
                             "default": "truth"},
                    "label": {"type": "string"},
                    "priority": {"type": "integer", "default": 0},
                },
                "required": ["project", "memory_id"],
            },
        ),
        types.Tool(
            name="unpin_memory",
            description="Remove a pin from the project working set.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "memory_id": {"type": "string"},
                },
                "required": ["project", "memory_id"],
            },
        ),
        types.Tool(
            name="list_pins",
            description="List pinned memories for a project (working set).",
            inputSchema={
                "type": "object",
                "properties": {"project": {"type": "string"}},
                "required": ["project"],
            },
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
    "search_memory":        (["query"], ["limit", "project", "type_filter", "days", "tags", "include_history"]),
    "get_memory":           (["memory_id"], []),
    "add_memory":           (["content", "type", "project"], ["tags", "source", "description"]),
    "delete_memory":        (["memory_id"], []),
    "get_recent_context":   ([], ["project", "days"]),
    "list_projects":        ([], []),
    "get_startup_summary":  ([], []),
    "get_related_memories": (["memory_id"], ["limit", "min_weight", "kinds", "include_archived"]),
    "get_memory_graph":     ([], ["project", "min_weight", "max_nodes", "include_archived"]),
    "consolidate_memory":   ([], ["project", "idle_days"]),
    "get_project_brief":    (["project"], ["intent", "max_chars", "include_system", "days"]),
    "list_conflicts":       ([], ["project", "limit"]),
    "resolve_conflict":     (["winner_id", "loser_id"], []),
    "dismiss_conflict":     (["a_id", "b_id"], []),
    "pin_memory":           (["project", "memory_id"], ["kind", "label", "priority"]),
    "unpin_memory":         (["project", "memory_id"], []),
    "list_pins":            (["project"], []),
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
    if "max_chars" in clean:
        clean["max_chars"] = _clamp_int(clean["max_chars"], 800, 12000, 3500)
    if "idle_days" in clean:
        clean["idle_days"] = _clamp_int(clean["idle_days"], 1, 365, 14)
    if "priority" in clean:
        clean["priority"] = _clamp_int(clean["priority"], -100, 100, 0)

    handlers = {
        "search_memory":        lambda a: handle_search_memory(**a),
        "get_memory":           lambda a: handle_get_memory(**a),
        "add_memory":           lambda a: handle_add_memory(**a),
        "delete_memory":        lambda a: handle_delete_memory(**a),
        "get_recent_context":   lambda a: handle_get_recent_context(**a),
        "list_projects":        lambda _: handle_list_projects(),
        "get_startup_summary":  lambda _: handle_get_startup_summary(),
        "get_related_memories": lambda a: handle_get_related_memories(**a),
        "get_memory_graph":     lambda a: handle_get_memory_graph(**a),
        "consolidate_memory":   lambda a: handle_consolidate_memory(**a),
        "get_project_brief":    lambda a: handle_get_project_brief(**a),
        "list_conflicts":       lambda a: handle_list_conflicts(**a),
        "resolve_conflict":     lambda a: handle_resolve_conflict(**a),
        "dismiss_conflict":     lambda a: handle_dismiss_conflict(**a),
        "pin_memory":           lambda a: handle_pin_memory(**a),
        "unpin_memory":         lambda a: handle_unpin_memory(**a),
        "list_pins":            lambda a: handle_list_pins(**a),
    }
    result = await handlers[name](clean)
    return [types.TextContent(type="text", text=result)]
