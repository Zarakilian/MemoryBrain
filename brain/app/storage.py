import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .db import connect
from .models import MemoryEntry, Project
from .migrations.runner import run_migrations

DB_PATH = Path("/app/data/brain.db")

# Edges the linker can always recompute. Everything else in memory_links is a
# verdict or a citation (derived_from, conflicts_with incl. dismissed, entity)
# and must survive a graph rebuild or an edit.
DERIVED_EDGE_KINDS = ("semantic", "tag", "reference", "session_chain")


def content_hash(content: str, project: str) -> str:
    return hashlib.sha256(f"{content}|{project}".encode()).hexdigest()


def init_db(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        # v0.4.x base schema only — v0.5.0+ columns (status, superseded_by, supersedes)
        # are added by migrations/001_add_status_supersession.sql at startup.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                summary TEXT DEFAULT '',
                type TEXT NOT NULL,
                project TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '',
                importance INTEGER DEFAULT 3,
                timestamp TEXT NOT NULL,
                chroma_id TEXT DEFAULT '',
                content_hash TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON memories(content_hash)")
        conn.commit()
    # Apply any pending migrations (adds status, superseded_by, supersedes etc.)
    run_migrations(db_path=db_path)
    with connect(db_path) as conn:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, summary, tags,
                content='memories', content_rowid='rowid'
            )
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content, summary, tags)
                VALUES (new.rowid, new.content, new.summary, new.tags);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, summary, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
            END
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content, summary, tags)
                VALUES ('delete', old.rowid, old.content, old.summary, old.tags);
                INSERT INTO memories_fts(rowid, content, summary, tags)
                VALUES (new.rowid, new.content, new.summary, new.tags);
            END
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                one_liner TEXT DEFAULT ''
            )
        """)
        conn.commit()


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    return connect(db_path)


def insert_memory(conn: sqlite3.Connection, entry: MemoryEntry) -> None:
    """INSERT one memory row on an open connection (the caller commits)."""
    h = content_hash(entry.content, entry.project)
    ts = entry.timestamp.isoformat()
    conn.execute(
        """INSERT INTO memories
           (id, content, summary, type, project, tags, source, importance,
            timestamp, content_hash, status, superseded_by, supersedes,
            writer, trust, embedded, valid_from, valid_to, invalidated_by,
            content_updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.id, entry.content, entry.summary, entry.type, entry.project,
            json.dumps(entry.tags), entry.source,
            3 if entry.importance is None else entry.importance,
            ts, h,
            entry.status, entry.superseded_by, entry.supersedes,
            entry.writer, entry.trust, int(entry.embedded), entry.valid_from,
            entry.valid_to, entry.invalidated_by, ts,
        ),
    )


def add_memory(entry: MemoryEntry, db_path: Path = DB_PATH):
    with _connect(db_path) as conn:
        insert_memory(conn, entry)
        conn.commit()


def close_superseded(conn: sqlite3.Connection, old_id: str, new_id: str, at: str,
                     actor: str = "ingest") -> None:
    """Close an active memory that a newer one replaces (the caller commits):
    archived, pointing at its replacement, valid until `at`, audited."""
    cur = conn.execute(
        """UPDATE memories SET status = 'archived', superseded_by = ?, invalidated_by = ?,
                  valid_to = ? WHERE id = ? AND status = 'active'""",
        (new_id, new_id, at, old_id),
    )
    if cur.rowcount:
        _audit(conn, old_id, "supersede", actor, "", {"by": new_id})


def count_chunks(memory_id: str, db_path: Path = DB_PATH) -> int:
    with _connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM vec_chunks WHERE memory_id = ?",
                            (memory_id,)).fetchone()[0]


def get_memory(memory_id: str, db_path: Path = DB_PATH) -> Optional[MemoryEntry]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return _row_to_entry(row) if row else None


def keyword_search(
    query: str,
    limit: int = 20,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
    db_path: Path = DB_PATH,
    match: Optional[str] = None,
) -> list[dict]:
    """FTS5 search ranked by BM25. `match` is a prebuilt MATCH expression
    (search.build_fts_query); without it every word is a required phrase."""
    if match is None:
        tokens = query.split()
        match = " ".join('"' + t.replace('"', '""') + '"' for t in tokens) if tokens else '""'
    with _connect(db_path) as conn:
        sql = """
            SELECT m.id, m.summary, substr(m.content, 1, 200) AS content_preview,
                   m.type, m.project, m.source, m.importance, m.timestamp, m.status,
                   snippet(memories_fts, 0, '', '', '…', 32) AS snippet
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
        """
        params: list = [match]
        if not include_history:
            sql += " AND m.status = 'active'"
        if project:
            sql += " AND m.project = ?"
            params.append(project)
        if type_filter:
            sql += " AND m.type = ?"
            params.append(type_filter)
        if days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            sql += " AND m.timestamp >= ?"
            params.append(cutoff)
        if tags:
            tag_clauses = " OR ".join(["m.tags LIKE ?" for _ in tags])
            sql += f" AND ({tag_clauses})"
            params.extend([f'%"{t}"%' for t in tags])
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = conn.execute(sql, params).fetchall()
        except Exception:
            return []
    return [dict(row) for row in rows]


def get_recent(
    project: Optional[str] = None,
    days: int = 7,
    limit: int = 20,
    include_history: bool = False,
    db_path: Path = DB_PATH,
) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect(db_path) as conn:
        sql = """SELECT id, summary, substr(content, 1, 200) AS content_preview,
                        type, project, source, importance, timestamp, status
                 FROM memories WHERE timestamp >= ?"""
        params: list = [cutoff]
        if not include_history:
            sql += " AND status = 'active'"
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def archive_memory(memory_id: str, superseded_by: str, db_path: Path = DB_PATH,
                   actor: str = "system", reason: str = ""):
    """Mark a memory as archived (superseded) with an audit row. Never deletes."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE memories SET status = 'archived', superseded_by = ? WHERE id = ?",
            (superseded_by, memory_id),
        )
        if cur.rowcount:
            _audit(conn, memory_id, "archive", actor, reason,
                   {"superseded_by": superseded_by} if superseded_by else None)
        conn.commit()


def set_supersedes(memory_id: str, supersedes: str, db_path: Path = DB_PATH):
    """Set the supersedes back-reference on a newly ingested memory."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE memories SET supersedes = ? WHERE id = ?",
            (supersedes, memory_id),
        )
        conn.commit()


def get_project_recent_state(project: str, db_path: Path = DB_PATH) -> str:
    """Return the summary of the most recent active memory for a project."""
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT summary, content FROM memories
               WHERE project = ? AND status = 'active'
               ORDER BY timestamp DESC LIMIT 1""",
            (project,),
        ).fetchone()
    if row is None:
        return ""
    return (row["summary"] or row["content"][:100]).strip()


STRENGTH_FLOOR = 0.2      # forgetting is ranking, never deletion
STRENGTH_CEIL = 3.0
RECALL_BOOST_DIRECT = 0.25   # explicitly fetched (get_memory, inspector)
RECALL_BOOST_SEARCH = 0.05   # surfaced in a search result


def record_recall(memory_ids: list[str], boost: float = RECALL_BOOST_DIRECT,
                  db_path: Path = DB_PATH) -> int:
    """Reinforcement: retrieval strengthens a memory (Ebbinghaus, inverted).
    Bounded so nothing can grow monstrous or vanish."""
    if not memory_ids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"""UPDATE memories
                SET strength = min(?, strength + ?), last_recalled = ?
                WHERE id IN ({','.join('?' * len(memory_ids))})""",
            [STRENGTH_CEIL, boost, now, *memory_ids],
        )
        conn.commit()
        return cur.rowcount


DECAY_GRACE_DAYS = 14
DECAY_HALF_LIFE_DAYS = 60
STRENGTH_FLOOR, STRENGTH_MAX = 0.2, 3.0
BELIEF_SOURCE_DAMP = 0.8  # an approved belief speaks first for its sources
# Only narrative memories fade with disuse. Facts, decisions, references,
# procedures, beliefs and open loops stay as strong as their reinforcement:
# an old truth must keep beating a fresh loose match (search v3).
DECAYING_TYPES = frozenset({"session", "handover", "note"})


def effective_strength(stored: float, timestamp: str, last_recalled: Optional[str],
                       pinned: bool, now: Optional[datetime] = None,
                       memory_type: Optional[str] = None) -> float:
    """Strength used for ranking (v3). The column holds reinforcement only;
    forgetting is computed from time, so running consolidation again never
    decays anything twice. Idle days count from the later of the last recall
    and the write, minus a 14-day grace; strength halves every 60 idle days.
    Pinned memories never decay, and only DECAYING_TYPES decay at all."""
    stored = 1.0 if stored is None else float(stored)
    if pinned or (memory_type is not None and memory_type not in DECAYING_TYPES):
        return min(STRENGTH_MAX, max(STRENGTH_FLOOR, stored))
    now = now or datetime.now(timezone.utc)
    moments = []
    for value in (timestamp, last_recalled):
        try:
            ts = datetime.fromisoformat(value) if value else None
        except ValueError:
            ts = None
        if ts is not None:
            moments.append(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))
    last = max(moments) if moments else now
    idle = max(0.0, (now - last).total_seconds() / 86400 - DECAY_GRACE_DAYS)
    return min(STRENGTH_MAX, max(STRENGTH_FLOOR, stored * 0.5 ** (idle / DECAY_HALF_LIFE_DAYS)))


def decay_strengths(idle_days: int = 14, factor: float = 0.9,
                    db_path: Path = DB_PATH) -> int:
    """v3: decay is computed at read time (effective_strength), so nothing is
    multiplied here. Returns how many unpinned memories are past their grace
    period, for the sleep report."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DECAY_GRACE_DAYS)).isoformat()
    with _connect(db_path) as conn:
        try:
            return conn.execute(
                f"""SELECT COUNT(*) FROM memories m
                   WHERE m.status = 'active' AND COALESCE(m.last_recalled, m.timestamp) < ?
                     AND m.type IN ({','.join('?' * len(DECAYING_TYPES))})
                     AND NOT EXISTS (SELECT 1 FROM project_pins p WHERE p.memory_id = m.id)""",
                (cutoff, *sorted(DECAYING_TYPES))).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def scale_strengths(memory_ids: list[str], factor: float,
                    db_path: Path = DB_PATH) -> int:
    """Dampen (or boost) specific memories — e.g. sources that a belief now
    represents sink a little; the belief speaks for them."""
    if not memory_ids:
        return 0
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"""UPDATE memories
                SET strength = max(?, min(?, strength * ?))
                WHERE id IN ({','.join('?' * len(memory_ids))})""",
            [STRENGTH_FLOOR, STRENGTH_CEIL, factor, *memory_ids],
        )
        conn.commit()
        return cur.rowcount


def get_strengths(memory_ids: list[str], db_path: Path = DB_PATH) -> dict[str, float]:
    """Effective strength map for ranking (see effective_strength) — read-only,
    shape-neutral (results never carry the column)."""
    if not memory_ids:
        return {}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT m.id, m.type, m.strength, m.timestamp, m.last_recalled,
                       EXISTS (SELECT 1 FROM project_pins p WHERE p.memory_id = m.id) AS pinned
                FROM memories m WHERE m.id IN ({','.join('?' * len(memory_ids))})""",
            memory_ids,
        ).fetchall()
    return {r["id"]: effective_strength(r["strength"], r["timestamp"], r["last_recalled"],
                                        bool(r["pinned"]), memory_type=r["type"]) for r in rows}


def set_belief_status(memory_id: str, approve: bool, actor: str,
                      db_path: Path = DB_PATH) -> bool:
    """Approve (active) or reject (archived) a proposed belief, audited. On
    approval the belief speaks first for its sources, which sink a little."""
    with _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE memories SET status = ? WHERE id = ? AND type = 'belief' AND status = 'proposed'",
            ("active" if approve else "archived", memory_id))
        if not cur.rowcount:
            return False
        _audit(conn, memory_id, "approve" if approve else "reject", actor)
        if approve:
            conn.execute(
                """UPDATE memories SET strength = max(?, strength * ?)
                   WHERE id IN (SELECT dst_id FROM memory_links
                                WHERE src_id = ? AND kind = 'derived_from')""",
                (STRENGTH_FLOOR, BELIEF_SOURCE_DAMP, memory_id))
        conn.commit()
    return True


def _row_to_project(row: sqlite3.Row) -> Project:
    keys = row.keys()
    return Project(
        slug=row["slug"], name=row["name"],
        last_activity=datetime.fromisoformat(row["last_activity"]),
        one_liner=row["one_liner"] or "",
        description=(row["description"] or "") if "description" in keys else "",
        description_source=(row["description_source"] or "") if "description_source" in keys else "",
    )


def upsert_project(project: Project, db_path: Path = DB_PATH):
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO projects (slug, name, last_activity, one_liner)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET
                   last_activity=excluded.last_activity,
                   one_liner=CASE WHEN excluded.one_liner != '' THEN excluded.one_liner
                                  ELSE projects.one_liner END""",
            (project.slug, project.name, project.last_activity.isoformat(), project.one_liner),
        )
        conn.commit()


def get_project(slug: str, db_path: Path = DB_PATH) -> Optional[Project]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    return _row_to_project(row)


def list_projects(db_path: Path = DB_PATH) -> list[Project]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY last_activity DESC").fetchall()
    return [_row_to_project(r) for r in rows]


def _audit(conn: sqlite3.Connection, memory_id: str, action: str, actor: str,
           reason: str = "", detail: Optional[dict] = None) -> None:
    conn.execute(
        """INSERT INTO memory_audit (id, memory_id, action, actor, reason, at, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (str(uuid.uuid4()), memory_id, action, actor or "", reason or "",
         datetime.now(timezone.utc).isoformat(), json.dumps(detail or {})),
    )


def audit(memory_id: str, action: str, actor: str, reason: str = "",
          detail: Optional[dict] = None, db_path: Path = DB_PATH) -> None:
    """Record who changed a memory's lifecycle, and why."""
    with _connect(db_path) as conn:
        _audit(conn, memory_id, action, actor, reason, detail)
        conn.commit()


def _set_status_audited(memory_id: str, status: str, action: str, actor: str,
                        reason: str, db_path: Path) -> bool:
    with _connect(db_path) as conn:
        cur = conn.execute("UPDATE memories SET status = ? WHERE id = ?", (status, memory_id))
        if cur.rowcount == 0:
            return False
        _audit(conn, memory_id, action, actor, reason)
        conn.commit()
    return True


def archive_memory_audited(memory_id: str, actor: str, reason: str = "",
                           db_path: Path = DB_PATH) -> bool:
    """Archive a memory (reversible) with an audit row. False if it does not exist."""
    return _set_status_audited(memory_id, "archived", "archive", actor, reason, db_path)


def restore_memory(memory_id: str, actor: str, db_path: Path = DB_PATH) -> bool:
    """Bring an archived memory back to active, with an audit row. A restored
    memory is valid again, so its closure (superseded_by, invalidated_by,
    valid_to) is cleared; the audit row keeps the old values."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT superseded_by, invalidated_by, valid_to FROM memories "
                           "WHERE id = ?", (memory_id,)).fetchone()
        if row is None:
            return False
        conn.execute("""UPDATE memories SET status = 'active', superseded_by = NULL,
                        invalidated_by = NULL, valid_to = NULL WHERE id = ?""", (memory_id,))
        _audit(conn, memory_id, "restore", actor, "",
               {k: row[k] for k in ("superseded_by", "invalidated_by", "valid_to") if row[k]})
        conn.commit()
    return True


def hard_delete_memory(memory_id: str, actor: str, reason: str = "",
                       db_path: Path = DB_PATH) -> bool:
    """Remove a memory for good: the row, its vectors and chunks, its edges in
    both directions, its pins and its file links. One audit row (no content)
    is the only trace left. False if it does not exist."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT project, type FROM memories WHERE id = ?",
                           (memory_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM memory_links WHERE src_id = ? OR dst_id = ?",
                     (memory_id, memory_id))
        conn.execute("DELETE FROM project_pins WHERE memory_id = ?", (memory_id,))
        conn.execute("DELETE FROM file_links WHERE src_kind = 'memory' AND src_id = ?",
                     (memory_id,))
        conn.execute("DELETE FROM vec_chunks WHERE memory_id = ?", (memory_id,))
        conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        _audit(conn, memory_id, "hard_delete", actor, reason,
               {"project": row["project"], "type": row["type"]})
        conn.commit()
    return True


def delete_memory(memory_id: str, db_path: Path = DB_PATH):
    """Hard delete. Used for: ChromaDB rollback, or explicit MCP delete_memory calls."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()


def get_memory_by_content_hash(content: str, project: str, db_path: Path = DB_PATH,
                               active_only: bool = False) -> Optional[MemoryEntry]:
    h = content_hash(content, project)
    sql = "SELECT * FROM memories WHERE content_hash = ?"
    if active_only:
        sql += " AND status = 'active'"
    with _connect(db_path) as conn:
        row = conn.execute(sql + " LIMIT 1", (h,)).fetchone()
    return _row_to_entry(row) if row else None


def get_meta(key: str, default: str = "", db_path: Path = DB_PATH) -> str:
    """Read a brain_meta key (v2.3). Missing table/key → default."""
    with _connect(db_path) as conn:
        try:
            row = conn.execute(
                "SELECT value FROM brain_meta WHERE key = ?", (key,)
            ).fetchone()
        except sqlite3.OperationalError:
            return default
    return row["value"] if row else default


def set_meta(key: str, value: str, db_path: Path = DB_PATH) -> None:
    """Upsert a brain_meta key (v2.3)."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO brain_meta (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value, updated_at=excluded.updated_at""",
            (key, value, now),
        )
        conn.commit()


def get_next_session_note(project: str, db_path: Path = DB_PATH) -> Optional[dict]:
    """The newest ACTIVE memory of THIS project tagged next_session, or None.
    No project means no note: one project's plan is never handed to another."""
    if not project:
        return None
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT id, content, writer, timestamp FROM memories m
               WHERE project = ? AND status = 'active'
                 AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(m.tags)
                                                     THEN m.tags ELSE '[]' END)
                             WHERE value = 'next_session')
               ORDER BY timestamp DESC LIMIT 1""",
            (project,),
        ).fetchone()
    return dict(row) if row else None


def get_next_session_notes(project: str = "", db_path: Path = DB_PATH) -> str:
    """Text-only form of get_next_session_note, kept for existing callers."""
    note = get_next_session_note(project, db_path=db_path)
    return note["content"] if note else ""


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    keys = row.keys()
    return MemoryEntry(
        id=row["id"], content=row["content"], summary=row["summary"],
        type=row["type"], project=row["project"],
        tags=json.loads(row["tags"]),
        source=row["source"], importance=row["importance"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        status=row["status"] if "status" in keys else "active",
        superseded_by=row["superseded_by"] if "superseded_by" in keys else None,
        supersedes=row["supersedes"] if "supersedes" in keys else None,
        writer=row["writer"] if "writer" in keys else "",
        trust=row["trust"] if "trust" in keys else "agent",
        embedded=bool(row["embedded"]) if "embedded" in keys else True,
        valid_from=row["valid_from"] if "valid_from" in keys else None,
        valid_to=row["valid_to"] if "valid_to" in keys else None,
        invalidated_by=row["invalidated_by"] if "invalidated_by" in keys else None,
    )
