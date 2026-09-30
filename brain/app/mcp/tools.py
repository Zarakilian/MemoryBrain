import json
import logging
import re
import sqlite3
from typing import Optional
from mcp.server import Server
import mcp.types as types

from ..storage import (get_memory, get_recent,
                       list_projects as storage_list_projects, archive_memory_audited,
                       get_project_recent_state, record_recall,
                       RECALL_BOOST_SEARCH, DB_PATH, _connect)
from ..search import search_with_status
from ..ingest_pipeline import ingest, write_report
from ..models import MemoryEntry

logger = logging.getLogger(__name__)

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
    # v2.3
    "record_retrieval",
    "get_timeline",
    "get_entities",
    "get_project_policy",
    "set_project_policy",
    # v2.4 — Synapse (Agent Exchange)
    "post_task",
    "get_agent_inbox",
    "reply_to_thread",
    "update_task_status",
    "list_threads",
    "get_thread",
    "get_agent_stats",
    # v2.5 - workspace layer
    "set_project_info",
    "get_workspace_map",
    "get_project_files",
    "get_file_context",
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
    results, degraded = await search_with_status(
        query, limit=limit, project=project, type_filter=type_filter,
        days=days, tags=tags, include_history=include_history,
    )
    try:
        record_recall([r["id"] for r in results],
                      boost=RECALL_BOOST_SEARCH, db_path=DB_PATH)
    except Exception:
        pass
    # v2.3: log impression for ranking feedback (no chosen_id yet)
    try:
        from ..retrieval import record_retrieval
        record_retrieval(
            query=query,
            result_ids=[r["id"] for r in results if r.get("id")],
            project=project,
            source="search_memory",
            db_path=DB_PATH,
        )
    except Exception:
        pass
    if degraded:
        # Keyword hits only; the object form tells the agent why.
        return json.dumps({"results": results, "degraded": degraded}, default=str)
    return json.dumps(results, default=str)


EXCERPT_MARGIN = 300


def _chunk_spans(memory_id: str, content: str) -> list[tuple[int, int]]:
    with _connect(DB_PATH) as conn:
        rows = conn.execute("SELECT start_char, end_char FROM vec_chunks WHERE memory_id = ? "
                            "ORDER BY chunk_ix", (memory_id,)).fetchall()
    if rows:
        return [(r["start_char"], r["end_char"]) for r in rows]
    from ..indexing import chunk_text
    return [(c.start, c.end) for c in chunk_text(content)] or [(0, len(content))]


def _excerpt_span(memory_id: str, content: str, around: str) -> tuple[int, int]:
    """The chunk that best matches `around`, plus EXCERPT_MARGIN either side:
    the chunk holding the exact phrase, else the one sharing the most words."""
    spans = _chunk_spans(memory_id, content)
    lowered, phrase = content.lower(), around.strip().lower()
    pos = lowered.find(phrase) if phrase else -1
    if pos >= 0:
        holding = [s for s in spans if s[0] <= pos and pos + len(phrase) <= s[1]]
        best = (holding or [s for s in spans if s[0] <= pos < s[1]] or spans)[0]
    else:
        words = {w for w in re.findall(r"[a-z0-9]{3,}", phrase)}
        best = max(spans, key=lambda s: sum(lowered[s[0]:s[1]].count(w) for w in words))
    return max(0, best[0] - EXCERPT_MARGIN), min(len(content), best[1] + EXCERPT_MARGIN)


async def handle_get_memory(memory_id: str, max_chars: Optional[int] = None,
                            around: Optional[str] = None) -> str:
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
        "content_chars": len(entry.content), "truncated": False,
    }
    if around:
        start, end = _excerpt_span(memory_id, entry.content, around)
        payload["content"] = entry.content[start:end]
        payload["excerpt"] = {"start": start, "end": end}
        payload["truncated"] = (start, end) != (0, len(entry.content))
    elif max_chars and len(entry.content) > max_chars:
        payload["content"] = entry.content[:max_chars]
        payload["truncated"] = True
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
    # v3 provenance: the writer is the caller's source (normalised) and trust is
    # always "agent" here; an MCP caller cannot claim a human wrote it.
    entry = MemoryEntry(content=content, type=type, project=project,
                        tags=tags or [], source=source,
                        writer=_writer_from_source(source) or "mcp", trust="agent")
    if description:
        entry.summary = description  # bypass LLM summariser
    try:
        result = await ingest(entry)
    except Exception as e:
        # Surface ValidationError cleanly to MCP clients
        return json.dumps({"error": str(e)})
    return json.dumps(write_report(result))


def _writer_from_source(source: str) -> str:
    return re.sub(r"[^a-z0-9@._:-]+", "-", (source or "").strip().lower()).strip("-")[:64]


async def handle_delete_memory(memory_id: str, reason: str = "") -> str:
    """v3: agents archive (reversible, audited). Hard delete is UI only."""
    if not archive_memory_audited(memory_id, actor="mcp", reason=reason, db_path=DB_PATH):
        return json.dumps({"error": f"Memory {memory_id} not found"})
    return json.dumps({"archived": True, "id": memory_id,
                       "restore": "brain_admin restore_memory"})


async def handle_get_recent_context(project: Optional[str] = None, days: int = 7) -> str:
    rows = get_recent(project=project, days=days, db_path=DB_PATH)
    return json.dumps(rows, default=str)


async def handle_list_projects() -> str:
    projects = storage_list_projects(db_path=DB_PATH)
    lines = ["## Projects\n"]
    for p in projects:
        lines.append(_identity_header(p.slug))
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
        line = f"- {_identity_header(p.slug)} (last: {p.last_activity.strftime('%Y-%m-%d')})"
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
                                    idle_days: int = 14,
                                    mode: str = "full") -> str:
    from ..consolidate import consolidate
    report = await consolidate(project=project or None, idle_days=idle_days,
                               mode=mode or "full")
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


async def handle_record_retrieval(
    query: str,
    result_ids: Optional[list] = None,
    chosen_id: Optional[str] = None,
    project: Optional[str] = None,
    source: str = "mcp",
) -> str:
    from ..retrieval import record_retrieval
    return json.dumps(record_retrieval(
        query=query, result_ids=result_ids or [], chosen_id=chosen_id,
        project=project, source=source or "mcp", db_path=DB_PATH,
    ))


async def handle_get_timeline(project: Optional[str] = None,
                              days: int = 30, limit: int = 100) -> str:
    from ..timeline import get_timeline
    return json.dumps(get_timeline(project=project, days=days, limit=limit,
                                   db_path=DB_PATH), default=str)


async def handle_get_entities(project: Optional[str] = None,
                              limit: int = 40) -> str:
    from ..timeline import get_entities
    return json.dumps(get_entities(project=project, limit=limit,
                                   db_path=DB_PATH), default=str)


async def handle_get_project_policy(project: str) -> str:
    from ..policy import get_policy
    return json.dumps(get_policy(project, db_path=DB_PATH), default=str)


async def handle_set_project_policy(
    project: str,
    include_system: Optional[bool] = None,
    max_brief_chars: Optional[int] = None,
    default_tags: Optional[list] = None,
    notes: Optional[str] = None,
) -> str:
    from ..policy import set_policy
    return json.dumps(set_policy(
        project,
        include_system=include_system,
        max_brief_chars=max_brief_chars,
        default_tags=default_tags,
        notes=notes,
        db_path=DB_PATH,
    ), default=str)


# ── v2.4 Synapse — Agent Exchange handlers ───────────────────────────────────

async def handle_post_task(
    project: str,
    title: str,
    body: str,
    from_agent: str,
    to_agent: str = "",
    kind: str = "task",
    priority: int = 0,
    refs: Optional[list] = None,
) -> str:
    from ..exchange import post_task
    try:
        return json.dumps(post_task(
            project=project, title=title, body=body, from_agent=from_agent,
            to_agent=to_agent, kind=kind, priority=priority, refs=refs,
            db_path=DB_PATH,
        ))
    except ValueError as e:
        return json.dumps({"error": str(e)})


async def handle_get_agent_inbox(
    agent: str,
    project: Optional[str] = None,
    include_broadcast: bool = True,
    mark_read: bool = True,
    limit: int = 20,
) -> str:
    from ..exchange import get_inbox
    try:
        return json.dumps(get_inbox(
            agent=agent, project=project, include_broadcast=include_broadcast,
            mark_read=mark_read, limit=limit, db_path=DB_PATH,
        ), default=str)
    except ValueError as e:
        return json.dumps({"error": str(e)})


async def handle_reply_to_thread(
    thread_id: str,
    body: str,
    from_agent: str,
    to_agent: str = "",
    intent: str = "update",
    refs: Optional[list] = None,
    status: Optional[str] = None,
) -> str:
    from ..exchange import reply_to_thread
    try:
        return json.dumps(reply_to_thread(
            thread_id=thread_id, body=body, from_agent=from_agent,
            to_agent=to_agent, intent=intent, refs=refs, status=status,
            db_path=DB_PATH,
        ))
    except ValueError as e:
        return json.dumps({"error": str(e)})


async def handle_update_task_status(
    thread_id: str,
    status: str,
    agent: str = "",
) -> str:
    from ..exchange import update_task_status
    try:
        return json.dumps(update_task_status(
            thread_id=thread_id, status=status, agent=agent, db_path=DB_PATH,
        ))
    except ValueError as e:
        return json.dumps({"error": str(e)})


async def handle_list_threads(
    project: Optional[str] = None,
    status: Optional[str] = None,
    agent: Optional[str] = None,
    limit: int = 50,
) -> str:
    from ..exchange import list_threads
    try:
        return json.dumps(list_threads(
            project=project, status=status, agent=agent, limit=limit,
            db_path=DB_PATH,
        ), default=str)
    except ValueError as e:
        return json.dumps({"error": str(e)})


async def handle_get_thread(thread_id: str) -> str:
    from ..exchange import get_thread
    result = get_thread(thread_id, db_path=DB_PATH)
    if result is None:
        return json.dumps({"error": f"Thread {thread_id} not found"})
    return json.dumps(result, default=str)


async def handle_get_agent_stats(
    project: Optional[str] = None,
    days: int = 90,
) -> str:
    from ..exchange import agent_stats, agent_network
    stats = agent_stats(project=project, days=days, db_path=DB_PATH)
    stats["network"] = agent_network(project=project, days=days,
                                     db_path=DB_PATH)
    return json.dumps(stats, default=str)


# ── v2.5 workspace layer handlers ────────────────────────────────────────────

def _identity_header(slug: str) -> str:
    from ..workspace.identity import get_identity, header_line
    try:
        return header_line(get_identity(slug, DB_PATH))
    except Exception:
        logger.warning("identity header failed for %s", slug, exc_info=True)
        return f"**{slug}**"


async def handle_set_project_info(project: str, name: Optional[str] = None,
                                  description: Optional[str] = None,
                                  home_path: Optional[str] = None,
                                  label: str = "", role: str = "primary") -> str:
    from ..workspace.identity import set_identity, header_line
    try:
        out = set_identity(project, db_path=DB_PATH, name=name, description=description,
                           source="tool", home_path=home_path, label=label or "", role=role or "primary")
    except ValueError as e:
        return json.dumps({"error": str(e),
                           "hint": "a project slug is lower-case letters, digits, '-' and '_', 1 to 64 characters"})
    out["header"] = header_line(out["identity"])
    return json.dumps(out, default=str)


async def handle_get_workspace_map(project: Optional[str] = None) -> str:
    from ..workspace.store import workspace_map
    return json.dumps(workspace_map(db_path=DB_PATH, project=project or None), default=str)


async def handle_get_project_files(project: str, folder: Optional[str] = None,
                                   ext: Optional[str] = None, sort: str = "ref_degree",
                                   limit: int = 50) -> str:
    from ..workspace.store import list_files
    files = list_files(project, db_path=DB_PATH, folder=folder, ext=ext, sort=sort, limit=limit)
    return json.dumps({"project": project, "count": len(files), "files": files}, default=str)


async def handle_get_file_context(path: Optional[str] = None, file_id: Optional[str] = None,
                                  project: Optional[str] = None) -> str:
    from ..workspace.store import file_context, get_file, resolve_abs_path, list_roots, find_files
    fid = file_id
    if not fid and path:
        try:
            resolved = resolve_abs_path(path, db_path=DB_PATH)
            if resolved:
                f = get_file(DB_PATH, root_id=resolved[0], rel_path=resolved[1])
                fid = f["file_id"] if f else None
            if not fid:
                for r in list_roots(DB_PATH):
                    f = get_file(DB_PATH, root_id=r["root_id"], rel_path=path)
                    if f:
                        fid = f["file_id"]
                        break
            if not fid:
                # no exact path hit: fall back to a loose name/title search so the
                # assistant can pass a file the way memories name it (find_file is phase 2)
                hits = find_files(path, DB_PATH, project=project, limit=5)
                if len(hits) == 1:
                    fid = hits[0]["file_id"]
                elif len(hits) > 1:
                    return json.dumps({"error": "ambiguous", "path": path,
                                       "candidates": [{"file_id": h["file_id"], "rel_path": h["rel_path"],
                                                       "project": h["project"]} for h in hits],
                                       "hint": "pass file_id, or a longer path, or project"})
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                raise
            return json.dumps({"error": "workspace index unavailable", "path": path,
                               "hint": "run `brain scan` on the host"})
    if not fid:
        return json.dumps({"error": "file not found", "path": path, "file_id": file_id,
                           "hint": "run `brain scan` on the host, or pass a root-relative path"})
    return json.dumps(file_context(fid, db_path=DB_PATH), default=str)


# ── MCP Server wiring ─────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_memory",
            description=("Hybrid keyword+semantic search. Returns summaries. Active memories "
                         "only by default. If the embedding model is down the reply is "
                         "{results, degraded} with keyword hits only."),
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
            description=("Fetch the content of a memory by ID. Beliefs include source citations. "
                         "For long memories pass around (a phrase) to get the matching region, "
                         "or max_chars to cap the length; content_chars is the full length."),
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "max_chars": {"type": "integer", "minimum": 100, "maximum": 100000},
                    "around": {"type": "string",
                               "description": "Return the best matching region of a long memory"},
                },
                "required": ["memory_id"],
            },
        ),
        types.Tool(
            name="add_memory",
            description=(
                "Store a new memory. Prefer type=fact/decision for durable truths, "
                "open_loop for unfinished work, session for narrative. "
                "Near-identical facts and decisions replace the older one; other near "
                "matches come back in potential_supersessions. Secrets are redacted. "
                "Pass description to skip the LLM summariser."
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
            description=("Archive a wrong memory (reversible, audited). Stale facts are "
                         "superseded automatically; a permanent delete is only possible in the "
                         "Atlas UI. Restore with brain_admin restore_memory."),
            inputSchema={
                "type": "object",
                "properties": {"memory_id": {"type": "string"},
                               "reason": {"type": "string"}},
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
                         "mode=light skips LLM beliefs (nightly-safe). Additive — never deletes."),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "idle_days": {"type": "integer", "default": 14,
                                  "description": "Memories unrecalled this long decay a little"},
                    "mode": {"type": "string", "enum": ["full", "light"], "default": "full"},
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
        types.Tool(
            name="record_retrieval",
            description=(
                "Log a search impression and optional chosen memory id so ranking "
                "can learn which results agents actually used."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "result_ids": {"type": "array", "items": {"type": "string"}},
                    "chosen_id": {"type": "string"},
                    "project": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_timeline",
            description="Chronological project (or global) activity: sessions, decisions, facts, beliefs, open loops.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "days": {"type": "integer", "default": 30},
                    "limit": {"type": "integer", "default": 100},
                },
            },
        ),
        types.Tool(
            name="get_entities",
            description="Entity cards for a project: tags, names, services, graph entities with mention counts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "limit": {"type": "integer", "default": 40},
                },
            },
        ),
        types.Tool(
            name="get_project_policy",
            description="Read per-project brief policy (include_system, max_brief_chars, notes, default_tags).",
            inputSchema={
                "type": "object",
                "properties": {"project": {"type": "string"}},
                "required": ["project"],
            },
        ),
        types.Tool(
            name="set_project_policy",
            description="Update per-project brief policy for get_project_brief.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "include_system": {"type": "boolean"},
                    "max_brief_chars": {"type": "integer"},
                    "default_tags": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
                "required": ["project"],
            },
        ),
        # v2.4 — Synapse (Agent Exchange)
        types.Tool(
            name="post_task",
            description=(
                "Open an agent-to-agent collaboration thread (task/review/"
                "question/handoff/discussion) with its first message. Address "
                "a specific agent via to_agent (claude/grok/codex/gemini) or "
                "leave empty to broadcast. Pass refs (memory ids, commits, "
                "file paths) instead of pasting large content."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "from_agent": {"type": "string",
                                   "description": "Who you are (claude/grok/codex/gemini)"},
                    "to_agent": {"type": "string",
                                 "description": "Recipient agent; empty = broadcast"},
                    "kind": {"type": "string",
                             "enum": ["task", "review", "question", "handoff", "discussion"],
                             "default": "task"},
                    "priority": {"type": "integer", "default": 0},
                    "refs": {"type": "array", "items": {"type": "string"},
                             "description": "Memory ids / commit hashes / file paths"},
                },
                "required": ["project", "title", "body", "from_agent"],
            },
        ),
        types.Tool(
            name="get_agent_inbox",
            description=(
                "Threads waiting on you: open threads assigned/addressed to "
                "you (or broadcast) with unread messages. Call at session "
                "start, right after get_startup_summary. Marks items read by "
                "default so they only reappear when someone writes again."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "agent": {"type": "string",
                              "description": "Who you are (claude/grok/codex/gemini)"},
                    "project": {"type": "string"},
                    "include_broadcast": {"type": "boolean", "default": True},
                    "mark_read": {"type": "boolean", "default": True},
                    "limit": {"type": "integer", "default": 20},
                },
                "required": ["agent"],
            },
        ),
        types.Tool(
            name="reply_to_thread",
            description=(
                "Reply in a collaboration thread. intent conveys the move "
                "(review/approval/answer/handoff/done…). Optionally set the "
                "thread status in the same call (e.g. status=review when "
                "requesting changes, status=done when finishing)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "body": {"type": "string"},
                    "from_agent": {"type": "string"},
                    "to_agent": {"type": "string"},
                    "intent": {"type": "string",
                               "enum": ["request", "update", "review", "approval",
                                        "question", "answer", "handoff", "done"],
                               "default": "update"},
                    "refs": {"type": "array", "items": {"type": "string"}},
                    "status": {"type": "string",
                               "enum": ["open", "in_progress", "review", "done", "closed"]},
                },
                "required": ["thread_id", "body", "from_agent"],
            },
        ),
        types.Tool(
            name="update_task_status",
            description="Move a thread through its lifecycle: open → in_progress → review → done/closed.",
            inputSchema={
                "type": "object",
                "properties": {
                    "thread_id": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["open", "in_progress", "review", "done", "closed"]},
                    "agent": {"type": "string"},
                },
                "required": ["thread_id", "status"],
            },
        ),
        types.Tool(
            name="list_threads",
            description=(
                "Browse collaboration threads by project/status/agent. "
                "status=any_open matches open, in_progress and review."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "status": {"type": "string",
                               "enum": ["open", "in_progress", "review",
                                        "done", "closed", "any_open"]},
                    "agent": {"type": "string",
                              "description": "Filter to threads created by or assigned to this agent"},
                    "limit": {"type": "integer", "default": 50},
                },
            },
        ),
        types.Tool(
            name="get_thread",
            description="Full transcript of one collaboration thread, oldest message first.",
            inputSchema={
                "type": "object",
                "properties": {"thread_id": {"type": "string"}},
                "required": ["thread_id"],
            },
        ),
        types.Tool(
            name="get_agent_stats",
            description=(
                "Multi-AI analytics: per-agent memory/message counts, "
                "per-project contribution shares, and the agent interaction "
                "network (who talks to whom)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "days": {"type": "integer", "default": 90},
                },
            },
        ),
        # v2.5 - workspace layer
        types.Tool(
            name="set_project_info",
            description=("Set a project's name, one-line description, and/or bind its home folder. "
                         "CALL THIS when the user says where a project or piece of work lives on disk "
                         "('this lives in', 'the folder for X is', 'all report files go in'); pass the "
                         "path as home_path. Never ask the user to map folders by hand."),
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string", "description": "1 to 3 plain sentences, max 400 chars"},
                    "home_path": {"type": "string", "description": "absolute path on this machine, or root-relative"},
                    "label": {"type": "string", "description": "e.g. 'reports working folder'"},
                    "role": {"type": "string", "enum": ["primary", "repo", "related", "area", "archive"]},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="get_workspace_map",
            description="Project headers with home folders, workspace roots per machine, unmapped and inferred folders, last scan time.",
            inputSchema={"type": "object", "properties": {"project": {"type": "string"}}},
        ),
        types.Tool(
            name="get_project_files",
            description="Indexed files under a project's bound folders (all depths). sort=ref_degree|recent|path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project": {"type": "string"},
                    "folder": {"type": "string", "description": "root-relative folder to narrow to"},
                    "ext": {"type": "string", "description": "e.g. md, sql, ps1"},
                    "sort": {"type": "string", "enum": ["ref_degree", "recent", "path"], "default": "ref_degree"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["project"],
            },
        ),
        types.Tool(
            name="get_file_context",
            description="Everything the brain knows about one file: metadata, memories that reference it (and how), files linking in/out, sessions that touched it. path may be absolute, root-relative, or just the file name; several matches come back as candidates.",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "absolute path, root-relative path, or bare file name"},
                    "file_id": {"type": "string"},
                    "project": {"type": "string"},
                },
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
    "get_memory":           (["memory_id"], ["max_chars", "around"]),
    "add_memory":           (["content", "type", "project"], ["tags", "source", "description"]),
    "delete_memory":        (["memory_id"], ["reason"]),
    "get_recent_context":   ([], ["project", "days"]),
    "list_projects":        ([], []),
    "get_startup_summary":  ([], []),
    "get_related_memories": (["memory_id"], ["limit", "min_weight", "kinds", "include_archived"]),
    "get_memory_graph":     ([], ["project", "min_weight", "max_nodes", "include_archived"]),
    "consolidate_memory":   ([], ["project", "idle_days", "mode"]),
    "get_project_brief":    (["project"], ["intent", "max_chars", "include_system", "days"]),
    "list_conflicts":       ([], ["project", "limit"]),
    "resolve_conflict":     (["winner_id", "loser_id"], []),
    "dismiss_conflict":     (["a_id", "b_id"], []),
    "pin_memory":           (["project", "memory_id"], ["kind", "label", "priority"]),
    "unpin_memory":         (["project", "memory_id"], []),
    "list_pins":            (["project"], []),
    "record_retrieval":     (["query"], ["result_ids", "chosen_id", "project", "source"]),
    "get_timeline":         ([], ["project", "days", "limit"]),
    "get_entities":         ([], ["project", "limit"]),
    "get_project_policy":   (["project"], []),
    "set_project_policy":   (["project"], ["include_system", "max_brief_chars", "default_tags", "notes"]),
    # v2.4 — Synapse (Agent Exchange)
    "post_task":            (["project", "title", "body", "from_agent"],
                             ["to_agent", "kind", "priority", "refs"]),
    "get_agent_inbox":      (["agent"], ["project", "include_broadcast", "mark_read", "limit"]),
    "reply_to_thread":      (["thread_id", "body", "from_agent"],
                             ["to_agent", "intent", "refs", "status"]),
    "update_task_status":   (["thread_id", "status"], ["agent"]),
    "list_threads":         ([], ["project", "status", "agent", "limit"]),
    "get_thread":           (["thread_id"], []),
    "get_agent_stats":      ([], ["project", "days"]),
    # v2.5 - workspace layer
    "set_project_info":     (["project"], ["name", "description", "home_path", "label", "role"]),
    "get_workspace_map":    ([], ["project"]),
    "get_project_files":    (["project"], ["folder", "ext", "sort", "limit"]),
    "get_file_context":     ([], ["path", "file_id", "project"]),
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
        if name == "get_memory":
            clean["max_chars"] = _clamp_int(clean["max_chars"], 100, 100_000, 100_000)
        else:
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
        "record_retrieval":     lambda a: handle_record_retrieval(**a),
        "get_timeline":         lambda a: handle_get_timeline(**a),
        "get_entities":         lambda a: handle_get_entities(**a),
        "get_project_policy":   lambda a: handle_get_project_policy(**a),
        "set_project_policy":   lambda a: handle_set_project_policy(**a),
        "post_task":            lambda a: handle_post_task(**a),
        "get_agent_inbox":      lambda a: handle_get_agent_inbox(**a),
        "reply_to_thread":      lambda a: handle_reply_to_thread(**a),
        "update_task_status":   lambda a: handle_update_task_status(**a),
        "list_threads":         lambda a: handle_list_threads(**a),
        "get_thread":           lambda a: handle_get_thread(**a),
        "get_agent_stats":      lambda a: handle_get_agent_stats(**a),
        "set_project_info":     lambda a: handle_set_project_info(**a),
        "get_workspace_map":    lambda a: handle_get_workspace_map(**a),
        "get_project_files":    lambda a: handle_get_project_files(**a),
        "get_file_context":     lambda a: handle_get_file_context(**a),
    }
    result = await handlers[name](clean)
    return [types.TextContent(type="text", text=result)]
