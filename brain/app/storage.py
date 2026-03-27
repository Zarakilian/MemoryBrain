import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .models import MemoryEntry, Project

DB_PATH = Path("/app/data/brain.db")


def init_db(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
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
                chroma_id TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content, summary, tags,
                content='memories',
                content_rowid='rowid'
            )
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
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
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO memories (id, content, summary, type, project, tags, source, importance, timestamp, chroma_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.id, entry.content, entry.summary, entry.type, entry.project,
                json.dumps(entry.tags), entry.source, entry.importance,
                entry.timestamp.isoformat(), entry.chroma_id,
            ),
        )
        conn.commit()


def get_memory(memory_id: str, db_path: Path = DB_PATH) -> Optional[MemoryEntry]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if row is None:
        return None
    return _row_to_entry(row)


def keyword_search(
    query: str,
    limit: int = 20,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    db_path: Path = DB_PATH,
) -> list[dict]:
    with _connect(db_path) as conn:
        sql = """
            SELECT m.id, m.summary, m.type, m.project, m.source, m.importance, m.timestamp
            FROM memories_fts
            JOIN memories m ON memories_fts.rowid = m.rowid
            WHERE memories_fts MATCH ?
        """
        params: list = [query]
        if project:
            sql += " AND m.project = ?"
            params.append(project)
        if type_filter:
            sql += " AND m.type = ?"
            params.append(type_filter)
        if days:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            sql += " AND m.timestamp >= ?"
            params.append(cutoff)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_recent(
    project: Optional[str] = None,
    days: int = 7,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[dict]:
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with _connect(db_path) as conn:
        sql = "SELECT id, summary, type, project, source, importance, timestamp FROM memories WHERE timestamp >= ?"
        params: list = [cutoff]
        if project:
            sql += " AND project = ?"
            params.append(project)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


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
    return Project(
        slug=row["slug"], name=row["name"],
        last_activity=datetime.fromisoformat(row["last_activity"]),
        one_liner=row["one_liner"],
    )


def list_projects(db_path: Path = DB_PATH) -> list[Project]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY last_activity DESC").fetchall()
    return [
        Project(
            slug=r["slug"], name=r["name"],
            last_activity=datetime.fromisoformat(r["last_activity"]),
            one_liner=r["one_liner"],
        )
        for r in rows
    ]


def _row_to_entry(row: sqlite3.Row) -> MemoryEntry:
    return MemoryEntry(
        id=row["id"], content=row["content"], summary=row["summary"],
        type=row["type"], project=row["project"],
        tags=json.loads(row["tags"]),
        source=row["source"], importance=row["importance"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        chroma_id=row["chroma_id"],
    )
