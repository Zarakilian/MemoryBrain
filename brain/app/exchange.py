# brain/app/exchange.py
"""Synapse — the Agent Exchange (v2.4).

Agent-to-agent collaboration threads on top of the shared brain. Threads are
operational (tasks, reviews, questions, handoffs); memories stay the knowledge
layer. Delivery is pull-based: agents call get_inbox at session start.

All functions accept db_path so tests can point at a temp database, mirroring
storage.py conventions.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect

THREAD_KINDS = ("task", "review", "question", "handoff", "discussion")
THREAD_STATUSES = ("open", "in_progress", "review", "done", "closed")
MESSAGE_INTENTS = ("request", "update", "review", "approval",
                   "question", "answer", "handoff", "done")
OPEN_STATUSES = ("open", "in_progress", "review")

MAX_BODY_LENGTH = 50_000
MAX_TITLE_LENGTH = 300

# Canonical agent names. Clients self-identify in many ways
# ("claude-code", "ChatGPT/Codex", "grok-cli") — normalize so addressing and
# analytics stay coherent.
_AGENT_ALIASES = (
    ("claude", "claude"),
    ("grok", "grok"),
    ("codex", "codex"),
    ("chatgpt", "codex"),
    ("openai", "codex"),
    ("gpt", "codex"),
    ("gemini", "gemini"),
    ("copilot", "copilot"),
    ("cowork", "claude"),
)


KNOWN_AGENTS = ("claude", "grok", "codex", "gemini", "copilot")


def normalize_agent(name: str) -> str:
    """Map a free-form agent identifier to a canonical slug."""
    low = (name or "").strip().lower()
    if not low:
        return ""
    for needle, canonical in _AGENT_ALIASES:
        if needle in low:
            return canonical
    slug = re.sub(r"[^a-z0-9_-]+", "-", low).strip("-")[:32]
    return slug or "unknown"


def attribute_source(source: str) -> str:
    """Attribute a memory's free-form `source` field to a known agent.

    Unlike normalize_agent (used for explicit exchange identities), this is
    strict: historical sources contain file names, project strings, hook
    labels, etc. Anything that isn't recognisably one of the known agents
    lands in the single 'other' bucket instead of becoming a pseudo-agent."""
    agent = normalize_agent(source)
    return agent if agent in KNOWN_AGENTS else "other"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate(value: str, allowed: tuple, label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} must be one of: {', '.join(allowed)}")
    return value


def _thread_row(r) -> dict[str, Any]:
    return {
        "id": r["id"], "project": r["project"], "title": r["title"],
        "kind": r["kind"], "status": r["status"],
        "created_by": r["created_by"], "assigned_to": r["assigned_to"],
        "priority": r["priority"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


def _message_row(r) -> dict[str, Any]:
    try:
        refs = json.loads(r["refs"] or "[]")
    except (ValueError, TypeError):
        refs = []
    return {
        "id": r["id"], "thread_id": r["thread_id"],
        "from_agent": r["from_agent"], "to_agent": r["to_agent"],
        "intent": r["intent"], "body": r["body"], "refs": refs,
        "created_at": r["created_at"],
    }


# ------------------------------------------------------------------ threads

def post_task(
    project: str,
    title: str,
    body: str,
    from_agent: str,
    to_agent: str = "",
    kind: str = "task",
    priority: int = 0,
    refs: Optional[list] = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Create a thread with its opening message."""
    if not title or not title.strip():
        raise ValueError("title must not be empty")
    if not body or not body.strip():
        raise ValueError("body must not be empty")
    if len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"body exceeds {MAX_BODY_LENGTH} character limit")
    _validate(kind, THREAD_KINDS, "kind")
    sender = normalize_agent(from_agent)
    if not sender:
        raise ValueError("from_agent must not be empty")
    recipient = normalize_agent(to_agent)

    thread_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    now = _now()
    intent = "request" if kind in ("task", "review", "question") else "handoff" \
        if kind == "handoff" else "update"
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO agent_threads
               (id, project, title, kind, status, created_by, assigned_to,
                priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)""",
            (thread_id, project, title.strip()[:MAX_TITLE_LENGTH], kind,
             sender, recipient, max(-100, min(100, int(priority))), now, now),
        )
        conn.execute(
            """INSERT INTO agent_messages
               (id, thread_id, from_agent, to_agent, intent, body, refs, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, thread_id, sender, recipient, intent, body,
             json.dumps(refs or []), now),
        )
        # The author has obviously read their own opening message.
        conn.execute(
            """INSERT INTO agent_read_cursors (agent, thread_id, last_read_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent, thread_id) DO UPDATE SET
                   last_read_at=excluded.last_read_at""",
            (sender, thread_id, now),
        )
        conn.commit()
    return {"thread_id": thread_id, "message_id": message_id,
            "status": "open", "assigned_to": recipient}


def reply_to_thread(
    thread_id: str,
    body: str,
    from_agent: str,
    to_agent: str = "",
    intent: str = "update",
    refs: Optional[list] = None,
    status: Optional[str] = None,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Append a message to a thread; optionally flip its status in one call."""
    if not body or not body.strip():
        raise ValueError("body must not be empty")
    if len(body) > MAX_BODY_LENGTH:
        raise ValueError(f"body exceeds {MAX_BODY_LENGTH} character limit")
    _validate(intent, MESSAGE_INTENTS, "intent")
    if status is not None:
        _validate(status, THREAD_STATUSES, "status")
    sender = normalize_agent(from_agent)
    if not sender:
        raise ValueError("from_agent must not be empty")
    recipient = normalize_agent(to_agent)

    now = _now()
    message_id = str(uuid.uuid4())
    with _connect(db_path) as conn:
        row = conn.execute("SELECT id FROM agent_threads WHERE id = ?",
                           (thread_id,)).fetchone()
        if row is None:
            return {"error": f"Thread {thread_id} not found"}
        conn.execute(
            """INSERT INTO agent_messages
               (id, thread_id, from_agent, to_agent, intent, body, refs, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (message_id, thread_id, sender, recipient, intent, body,
             json.dumps(refs or []), now),
        )
        if status is not None:
            conn.execute(
                "UPDATE agent_threads SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, thread_id),
            )
        else:
            conn.execute(
                "UPDATE agent_threads SET updated_at = ? WHERE id = ?",
                (now, thread_id),
            )
        conn.execute(
            """INSERT INTO agent_read_cursors (agent, thread_id, last_read_at)
               VALUES (?, ?, ?)
               ON CONFLICT(agent, thread_id) DO UPDATE SET
                   last_read_at=excluded.last_read_at""",
            (sender, thread_id, now),
        )
        conn.commit()
    out = {"message_id": message_id, "thread_id": thread_id}
    if status is not None:
        out["status"] = status
    return out


def update_task_status(
    thread_id: str,
    status: str,
    agent: str = "",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    _validate(status, THREAD_STATUSES, "status")
    now = _now()
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE agent_threads SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, thread_id),
        )
        conn.commit()
    if cur.rowcount == 0:
        return {"error": f"Thread {thread_id} not found"}
    return {"thread_id": thread_id, "status": status,
            "updated_by": normalize_agent(agent)}


def get_thread(thread_id: str, db_path: Path = DB_PATH) -> Optional[dict[str, Any]]:
    """Full transcript of one thread."""
    with _connect(db_path) as conn:
        t = conn.execute("SELECT * FROM agent_threads WHERE id = ?",
                         (thread_id,)).fetchone()
        if t is None:
            return None
        msgs = conn.execute(
            """SELECT * FROM agent_messages WHERE thread_id = ?
               ORDER BY created_at ASC""",
            (thread_id,),
        ).fetchall()
    out = _thread_row(t)
    out["messages"] = [_message_row(m) for m in msgs]
    return out


def list_threads(
    project: Optional[str] = None,
    status: Optional[str] = None,
    agent: Optional[str] = None,
    limit: int = 50,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Browse threads. agent filters to created_by OR assigned_to."""
    sql = """SELECT t.*,
                    (SELECT COUNT(*) FROM agent_messages m
                      WHERE m.thread_id = t.id) AS message_count
             FROM agent_threads t WHERE 1=1"""
    params: list[Any] = []
    if project:
        sql += " AND t.project = ?"
        params.append(project)
    if status:
        if status == "any_open":
            sql += f" AND t.status IN ({','.join('?' * len(OPEN_STATUSES))})"
            params.extend(OPEN_STATUSES)
        else:
            _validate(status, THREAD_STATUSES, "status")
            sql += " AND t.status = ?"
            params.append(status)
    if agent:
        a = normalize_agent(agent)
        sql += " AND (t.created_by = ? OR t.assigned_to = ?)"
        params.extend([a, a])
    sql += " ORDER BY t.updated_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    threads = []
    for r in rows:
        t = _thread_row(r)
        t["message_count"] = r["message_count"]
        threads.append(t)
    return {"threads": threads, "total": len(threads)}


# -------------------------------------------------------------------- inbox

def get_inbox(
    agent: str,
    project: Optional[str] = None,
    include_broadcast: bool = True,
    mark_read: bool = True,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Open threads with unread messages waiting on this agent.

    A thread is waiting on `agent` when it is open/in_progress/review AND
    (assigned to the agent, OR contains a message addressed to it, OR —
    when include_broadcast — contains a broadcast message from someone else),
    AND has messages newer than the agent's read cursor.

    mark_read=True (default) advances the cursor so the same items do not
    reappear next session unless someone writes again.
    """
    me = normalize_agent(agent)
    if not me:
        raise ValueError("agent must not be empty")
    now = _now()
    placeholders = ",".join("?" * len(OPEN_STATUSES))
    sql = f"""
        SELECT t.*, COALESCE(c.last_read_at, '') AS last_read_at
        FROM agent_threads t
        LEFT JOIN agent_read_cursors c
               ON c.thread_id = t.id AND c.agent = ?
        WHERE t.status IN ({placeholders})
    """
    params: list[Any] = [me, *OPEN_STATUSES]
    if project:
        sql += " AND t.project = ?"
        params.append(project)
    sql += """
        AND EXISTS (
            SELECT 1 FROM agent_messages m
            WHERE m.thread_id = t.id
              AND m.created_at > COALESCE(c.last_read_at, '')
              AND m.from_agent != ?
              AND (m.to_agent = ? OR t.assigned_to = ?
                   {broadcast})
        )
        ORDER BY t.priority DESC, t.updated_at DESC
        LIMIT ?
    """.format(broadcast="OR m.to_agent = ''" if include_broadcast else "")
    params.extend([me, me, me, max(1, min(int(limit), 100))])

    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
        items = []
        for r in rows:
            t = _thread_row(r)
            unread = conn.execute(
                """SELECT * FROM agent_messages
                   WHERE thread_id = ? AND created_at > ?
                   ORDER BY created_at ASC LIMIT 20""",
                (r["id"], r["last_read_at"]),
            ).fetchall()
            t["unread_messages"] = [_message_row(m) for m in unread]
            t["unread_count"] = len(unread)
            items.append(t)
        if mark_read and items:
            for t in items:
                conn.execute(
                    """INSERT INTO agent_read_cursors (agent, thread_id, last_read_at)
                       VALUES (?, ?, ?)
                       ON CONFLICT(agent, thread_id) DO UPDATE SET
                           last_read_at=excluded.last_read_at""",
                    (me, t["id"], now),
                )
            conn.commit()
    return {"agent": me, "threads": items, "total": len(items)}


# ---------------------------------------------------------------- analytics

def agent_stats(
    project: Optional[str] = None,
    days: int = 90,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Per-agent usage: memories written (via normalized source), exchange
    messages sent, threads opened, last activity. Global and per-project."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    totals: dict[str, dict[str, Any]] = {}
    per_project: dict[str, dict[str, dict[str, int]]] = {}

    def bump(agent: str, proj: str, field: str, ts: str = "") -> None:
        a = totals.setdefault(agent, {
            "agent": agent, "memories": 0, "messages": 0,
            "threads_opened": 0, "last_active": "",
        })
        a[field] += 1
        if ts and ts > a["last_active"]:
            a["last_active"] = ts
        p = per_project.setdefault(proj, {}).setdefault(agent, {
            "memories": 0, "messages": 0,
        })
        if field in p:
            p[field] += 1

    with _connect(db_path) as conn:
        mem_sql = """SELECT source, project, timestamp FROM memories
                     WHERE status = 'active' AND timestamp >= ?"""
        mem_params: list[Any] = [cutoff]
        if project:
            mem_sql += " AND project = ?"
            mem_params.append(project)
        for r in conn.execute(mem_sql, mem_params).fetchall():
            bump(attribute_source(r["source"]), r["project"],
                 "memories", r["timestamp"])

        try:
            msg_sql = """SELECT m.from_agent, t.project, m.created_at
                         FROM agent_messages m
                         JOIN agent_threads t ON t.id = m.thread_id
                         WHERE m.created_at >= ?"""
            msg_params: list[Any] = [cutoff]
            if project:
                msg_sql += " AND t.project = ?"
                msg_params.append(project)
            for r in conn.execute(msg_sql, msg_params).fetchall():
                bump(r["from_agent"] or "other", r["project"],
                     "messages", r["created_at"])

            th_sql = """SELECT created_by, project, created_at FROM agent_threads
                        WHERE created_at >= ?"""
            th_params: list[Any] = [cutoff]
            if project:
                th_sql += " AND project = ?"
                th_params.append(project)
            for r in conn.execute(th_sql, th_params).fetchall():
                bump(r["created_by"] or "other", r["project"],
                     "threads_opened", r["created_at"])
        except Exception:
            pass  # pre-007 DB mid-migration: memories-only stats

    agents = sorted(
        totals.values(),
        key=lambda a: (a["agent"] != "other",           # 'other' always last
                       a["memories"] + a["messages"] * 2),
        reverse=True,
    )
    projects_out = [
        {"project": proj,
         "agents": [{"agent": a, **counts}
                    for a, counts in sorted(
                        agents_map.items(),
                        key=lambda kv: kv[1]["memories"] + kv[1]["messages"],
                        reverse=True)]}
        for proj, agents_map in sorted(per_project.items())
    ]
    return {"days": days, "agents": agents, "projects": projects_out}


def agent_network(
    project: Optional[str] = None,
    days: int = 90,
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Interaction graph: who talks to whom, how often, in which projects.

    Explicitly addressed messages create a direct edge. Broadcast messages
    create an edge to the previous distinct speaker in the thread (a reply
    into the room answers whoever spoke last).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sql = """SELECT m.thread_id, m.from_agent, m.to_agent, m.created_at,
                    t.project
             FROM agent_messages m
             JOIN agent_threads t ON t.id = m.thread_id
             WHERE m.created_at >= ?"""
    params: list[Any] = [cutoff]
    if project:
        sql += " AND t.project = ?"
        params.append(project)
    sql += " ORDER BY m.thread_id, m.created_at ASC"

    edges: dict[tuple, dict[str, Any]] = {}
    nodes: dict[str, dict[str, Any]] = {}
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            rows = []
    prev_by_thread: dict[str, str] = {}
    for r in rows:
        src = r["from_agent"] or "other"
        dst = r["to_agent"] or ""
        if not dst:
            prev = prev_by_thread.get(r["thread_id"], "")
            dst = prev if (prev and prev != src) else ""
        node = nodes.setdefault(src, {"agent": src, "messages": 0,
                                      "projects": set()})
        node["messages"] += 1
        node["projects"].add(r["project"])
        if dst and dst != src:
            nodes.setdefault(dst, {"agent": dst, "messages": 0,
                                   "projects": set()})
            nodes[dst]["projects"].add(r["project"])
            key = (src, dst)
            e = edges.setdefault(key, {"source": src, "target": dst,
                                       "count": 0, "projects": set(),
                                       "last_at": ""})
            e["count"] += 1
            e["projects"].add(r["project"])
            if r["created_at"] > e["last_at"]:
                e["last_at"] = r["created_at"]
        prev_by_thread[r["thread_id"]] = src

    return {
        "days": days,
        "nodes": [{**n, "projects": sorted(n["projects"])}
                  for n in nodes.values()],
        "edges": [{**e, "projects": sorted(e["projects"])}
                  for e in edges.values()],
    }
