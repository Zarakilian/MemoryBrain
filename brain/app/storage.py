import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryEntry, Project
from .migrations.runner import run_migrations

DB_PATH = Path("/app/data/brain.db")


def content_hash(content: str, project: str) -> str:
    return hashlib.sha256(f"{content}|{project}".encode()).hexdigest()


def init_db(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
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
    with sqlite3.connect(db_path) as conn:
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
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def add_memory(entry: MemoryEntry, db_path: Path = DB_PATH):
    h = content_hash(entry.content, entry.project)
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO memories
               (id, content, summary, type, project, tags, source, importance,
                timestamp, content_hash, status, superseded_by, supersedes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id, entry.content, entry.summary, entry.type, entry.project,
                json.dumps(entry.tags), entry.source, entry.importance,
                entry.timestamp.isoformat(), h,
                entry.status, entry.superseded_by, entry.supersedes,
            ),
        )
        conn.commit()


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
) -> list[dict]:
    tokens = query.split()
    safe_query = " ".join('"' + t.replace('"', '""') + '"' for t in tokens) if tokens else '""'
    with _connect(db_path) as conn:
        sql = """
            SELECT m.id, m.summary, substr(m.content, 1, 200) AS content_preview,
                   m.type, m.project, m.source, m.importance, m.timestamp, m.status
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
        """
        params: list = [safe_query]
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


def archive_memory(memory_id: str, superseded_by: str, db_path: Path = DB_PATH):
    """Mark a memory as archived (superseded). Never deletes."""
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE memories SET status = 'archived', superseded_by = ? WHERE id = ?",
            (superseded_by, memory_id),
        )
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


def upsert_project(project: Project, db_path: Path = DB_PATH):
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO projects (slug, name, last_activity, one_liner)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET
                   last_activity=excluded.last_activity,
                   one_liner=excluded.one_liner""",
            (project.slug, project.name, project.last_activity.isoformat(), project.one_liner),
        )
        conn.commit()


def get_project(slug: str, db_path: Path = DB_PATH) -> Optional[Project]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    return Project(slug=row["slug"], name=row["name"],
                   last_activity=datetime.fromisoformat(row["last_activity"]),
                   one_liner=row["one_liner"])


def list_projects(db_path: Path = DB_PATH) -> list[Project]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY last_activity DESC").fetchall()
    return [Project(slug=r["slug"], name=r["name"],
                    last_activity=datetime.fromisoformat(r["last_activity"]),
                    one_liner=r["one_liner"]) for r in rows]


def delete_memory(memory_id: str, db_path: Path = DB_PATH):
    """Hard delete. Used for: ChromaDB rollback, or explicit MCP delete_memory calls."""
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()


def get_memory_by_content_hash(content: str, project: str, db_path: Path = DB_PATH) -> Optional[MemoryEntry]:
    h = content_hash(content, project)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM memories WHERE content_hash = ? LIMIT 1", (h,)
        ).fetchone()
    return _row_to_entry(row) if row else None


def get_next_session_notes(project: str = "", db_path: Path = DB_PATH) -> str:
    with _connect(db_path) as conn:
        if project:
            row = conn.execute(
                "SELECT content FROM memories WHERE project = ? AND tags LIKE ? ORDER BY timestamp DESC LIMIT 1",
                (project, '%next_session%'),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT content FROM memories WHERE tags LIKE ? ORDER BY timestamp DESC LIMIT 1",
                ('%next_session%',),
            ).fetchone()
    return row["content"] if row else ""


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
    )
