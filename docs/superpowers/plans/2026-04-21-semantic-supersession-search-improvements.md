# MemoryBrain v0.5.0 — Semantic Supersession & Search Improvements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add automatic semantic supersession of stale memories, recency-decay search, tag/type filters, Gemini/OpenAI AI provider support, `brain update` multi-machine upgrade command, and a `delete_memory` MCP tool.

**Architecture:** Migration system handles schema evolution idempotently at startup. Supersession engine runs post-embed pre-write in the ingest pipeline — similarity scan against active memories, type-aware thresholds, atomic archive. Search layer gains recency decay on RRF scores and tag/type filters. Summarise layer becomes a provider protocol (Ollama/Gemini/OpenAI) selected via env vars.

**Tech Stack:** FastAPI, SQLite FTS5, ChromaDB, Ollama, `google-generativeai` (optional), `openai` (optional), pytest, Docker Compose.

**Run tests inside Docker:**
```bash
cd /path/to/MemoryBrain
docker compose exec brain pytest brain/tests/ -v
```

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `brain/app/migrations/__init__.py` | Create | Package marker |
| `brain/app/migrations/runner.py` | Create | Idempotent migration runner, called at startup |
| `brain/app/migrations/001_add_status_supersession.sql` | Create | Adds status, superseded_by, supersedes columns + index |
| `brain/app/models.py` | Modify | Add status, superseded_by, supersedes fields to MemoryEntry; add `reference` to VALID_TYPES |
| `brain/app/storage.py` | Modify | Status filter on all queries; `archive_memory()`; `get_project_recent_state()`; tag filter in keyword_search |
| `brain/app/chroma.py` | Modify | Include `status` in metadata on `chroma_add`; add `chroma_update_metadata()` |
| `brain/app/ingest_pipeline.py` | Modify | Add `_check_supersession()` step post-embed, pre-write; return superseded/potential on entry |
| `brain/app/search.py` | Modify | Recency decay on RRF; `include_history` param; env var `RECENCY_DECAY_RATE` |
| `brain/app/mcp/tools.py` | Modify | Update `search_memory`, `add_memory`; add `delete_memory`; update `get_startup_summary` |
| `brain/app/summarise.py` | Rewrite | Provider protocol — Ollama / Gemini / OpenAI, auto-selected from env vars |
| `brain/app/main.py` | Modify | Call migration runner in lifespan; import StarletteHTTPException (already in v0.4.1) |
| `brain/requirements.txt` | Modify | Add `google-generativeai`, `openai` |
| `.env.example` | Modify | Add Gemini/OpenAI vars, RECENCY_DECAY_RATE |
| `cli/brain.py` | Modify | Add `brain update` command |
| `VERSION` | Modify | Bump to 0.5.0 |
| `brain/tests/test_migrations.py` | Create | Tests for migration runner |
| `brain/tests/test_supersession.py` | Create | Tests for supersession engine |
| `brain/tests/test_search.py` | Create | Tests for recency decay and filters |

---

## Task 1: Migration System

**Files:**
- Create: `brain/app/migrations/__init__.py`
- Create: `brain/app/migrations/runner.py`
- Create: `brain/app/migrations/001_add_status_supersession.sql`
- Create: `brain/tests/test_migrations.py`

- [ ] **Step 1: Write the failing tests**

```python
# brain/tests/test_migrations.py
import sqlite3
import tempfile
from pathlib import Path
import pytest
from app.migrations.runner import run_migrations


def _make_minimal_db(path: Path):
    """Create a db with the base memories table (no status columns yet)."""
    with sqlite3.connect(path) as conn:
        conn.execute("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT, summary TEXT DEFAULT '',
                type TEXT, project TEXT, tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '', importance INTEGER DEFAULT 3,
                timestamp TEXT, chroma_id TEXT DEFAULT '', content_hash TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE TABLE projects (slug TEXT PRIMARY KEY, name TEXT, last_activity TEXT, one_liner TEXT DEFAULT '')")
        conn.commit()


def test_migration_001_adds_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "brain.db"
        _make_minimal_db(db)
        run_migrations(db_path=db)
        with sqlite3.connect(db) as conn:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        assert "status" in cols
        assert "superseded_by" in cols
        assert "supersedes" in cols


def test_migration_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "brain.db"
        _make_minimal_db(db)
        run_migrations(db_path=db)
        run_migrations(db_path=db)  # running twice must not raise
        with sqlite3.connect(db) as conn:
            count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
        assert count == 1


def test_migration_creates_schema_migrations_table():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "brain.db"
        _make_minimal_db(db)
        run_migrations(db_path=db)
        with sqlite3.connect(db) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "schema_migrations" in tables


def test_migration_default_status_is_active():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "brain.db"
        _make_minimal_db(db)
        run_migrations(db_path=db)
        with sqlite3.connect(db) as conn:
            conn.execute("INSERT INTO memories (id, content, type, project, timestamp) VALUES ('t1','x','note','p','2026-01-01T00:00:00')")
            conn.commit()
            row = conn.execute("SELECT status FROM memories WHERE id='t1'").fetchone()
        assert row[0] == "active"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker compose exec brain pytest brain/tests/test_migrations.py -v
```
Expected: `ImportError: No module named 'app.migrations'`

- [ ] **Step 3: Create the migration package and SQL**

```bash
# brain/app/migrations/__init__.py  (empty)
touch brain/app/migrations/__init__.py
```

```sql
-- brain/app/migrations/001_add_status_supersession.sql
ALTER TABLE memories ADD COLUMN status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE memories ADD COLUMN superseded_by TEXT DEFAULT NULL;
ALTER TABLE memories ADD COLUMN supersedes TEXT DEFAULT NULL;
CREATE INDEX IF NOT EXISTS idx_status ON memories(status);
```

- [ ] **Step 4: Write the migration runner**

```python
# brain/app/migrations/runner.py
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATIONS_DIR = Path(__file__).parent


def run_migrations(db_path: Path) -> None:
    """Apply any unapplied *.sql migrations from this directory. Idempotent."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL
            )
        """)
        conn.commit()

        applied = {
            row[0]
            for row in conn.execute("SELECT filename FROM schema_migrations").fetchall()
        }

        for mf in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if mf.name in applied:
                continue
            conn.executescript(mf.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                (mf.name, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
```

- [ ] **Step 5: Run tests to confirm they pass**

```bash
docker compose exec brain pytest brain/tests/test_migrations.py -v
```
Expected: 4 tests PASS

- [ ] **Step 6: Commit**

```bash
git add brain/app/migrations/ brain/tests/test_migrations.py
git commit -m "feat: add versioned migration system with 001_add_status_supersession"
```

---

## Task 2: Update Models and Storage

**Files:**
- Modify: `brain/app/models.py`
- Modify: `brain/app/storage.py`

- [ ] **Step 1: Write failing tests**

```python
# brain/tests/test_storage_v2.py
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest
from app.models import MemoryEntry
from app.storage import init_db, add_memory, keyword_search, get_recent, archive_memory, get_project_recent_state
from app.migrations.runner import run_migrations


def _fresh_db() -> Path:
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "brain.db"
    init_db(db)
    run_migrations(db_path=db)
    return db


def _entry(project="p", type_="note", content="hello world") -> MemoryEntry:
    return MemoryEntry(content=content, type=type_, project=project,
                       timestamp=datetime.now(timezone.utc))


def test_add_memory_default_status_active():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    results = keyword_search("hello", project="p", db_path=db)
    assert len(results) == 1
    assert results[0]["status"] == "active"


def test_keyword_search_excludes_archived_by_default():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="other-id", db_path=db)
    results = keyword_search("hello", project="p", db_path=db)
    assert len(results) == 0


def test_keyword_search_include_history():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="other-id", db_path=db)
    results = keyword_search("hello", project="p", include_history=True, db_path=db)
    assert len(results) == 1
    assert results[0]["status"] == "archived"


def test_keyword_search_tag_filter():
    db = _fresh_db()
    e1 = _entry(content="tagged entry")
    e1.tags = ["sql", "investigation"]
    e2 = _entry(content="untagged entry")
    add_memory(e1, db_path=db)
    add_memory(e2, db_path=db)
    results = keyword_search("entry", project="p", tags=["sql"], db_path=db)
    assert len(results) == 1
    assert "tagged" in results[0]["content_preview"]


def test_get_recent_excludes_archived_by_default():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="other-id", db_path=db)
    results = get_recent(project="p", db_path=db)
    assert len(results) == 0


def test_archive_memory_sets_superseded_by():
    db = _fresh_db()
    e = _entry()
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="new-id", db_path=db)
    import sqlite3
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, superseded_by FROM memories WHERE id=?", (e.id,)).fetchone()
    assert row["status"] == "archived"
    assert row["superseded_by"] == "new-id"


def test_get_project_recent_state():
    db = _fresh_db()
    e = _entry(content="fixed the bug in deployment")
    e.summary = "Deployment bug fixed"
    add_memory(e, db_path=db)
    state = get_project_recent_state("p", db_path=db)
    assert "Deployment bug fixed" in state


def test_get_project_recent_state_excludes_archived():
    db = _fresh_db()
    e = _entry(content="old stale note")
    e.summary = "Old note"
    add_memory(e, db_path=db)
    archive_memory(e.id, superseded_by="x", db_path=db)
    state = get_project_recent_state("p", db_path=db)
    assert state == ""
```

- [ ] **Step 2: Run to confirm they fail**

```bash
docker compose exec brain pytest brain/tests/test_storage_v2.py -v
```
Expected: `ImportError` — `archive_memory`, `get_project_recent_state` don't exist yet.

- [ ] **Step 3: Update models.py**

Add `reference` to `VALID_TYPES` and new fields to `MemoryEntry`:

```python
# brain/app/models.py — full replacement
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
import uuid


def utcnow() -> datetime:
    return datetime.now(timezone.utc)

VALID_TYPES = {"note", "fact", "session", "handover", "file", "reference"}
MAX_CONTENT_LENGTH = 100_000
MAX_TAGS = 20
MAX_TAG_LENGTH = 100
PROJECT_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,64}$")


class ValidationError(ValueError):
    pass


@dataclass
class MemoryEntry:
    content: str
    type: str
    project: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    summary: str = ""
    tags: list = field(default_factory=list)
    source: str = ""
    importance: int = 3
    timestamp: datetime = field(default_factory=utcnow)
    # Lifecycle fields (persisted)
    status: str = "active"
    superseded_by: Optional[str] = None
    supersedes: Optional[str] = None
    # Transient fields (returned from ingest, never stored)
    superseded: list = field(default_factory=list)
    potential_supersessions: list = field(default_factory=list)


@dataclass
class Project:
    slug: str
    name: str
    last_activity: datetime = field(default_factory=utcnow)
    one_liner: str = ""


def validate_entry(entry: MemoryEntry) -> None:
    if not entry.content or not entry.content.strip():
        raise ValidationError("content must not be empty")
    if len(entry.content) > MAX_CONTENT_LENGTH:
        raise ValidationError(f"content exceeds {MAX_CONTENT_LENGTH} character limit")
    if entry.type not in VALID_TYPES:
        raise ValidationError(f"type must be one of: {', '.join(sorted(VALID_TYPES))}")
    if not PROJECT_SLUG_RE.match(entry.project):
        raise ValidationError("project must match ^[a-z0-9_-]{1,64}$")
    entry.importance = max(1, min(5, entry.importance))
    if len(entry.tags) > MAX_TAGS:
        raise ValidationError(f"too many tags (max {MAX_TAGS})")
    for tag in entry.tags:
        if len(tag) > MAX_TAG_LENGTH:
            raise ValidationError(f"tag exceeds {MAX_TAG_LENGTH} character limit")
```

- [ ] **Step 4: Update storage.py**

Replace the full file:

```python
# brain/app/storage.py
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryEntry, Project

DB_PATH = Path("/app/data/brain.db")


def content_hash(content: str, project: str) -> str:
    return hashlib.sha256(f"{content}|{project}".encode()).hexdigest()


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
                chroma_id TEXT DEFAULT '',
                content_hash TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                superseded_by TEXT DEFAULT NULL,
                supersedes TEXT DEFAULT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_content_hash ON memories(content_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON memories(status)")
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
    return MemoryEntry(
        id=row["id"], content=row["content"], summary=row["summary"],
        type=row["type"], project=row["project"],
        tags=json.loads(row["tags"]),
        source=row["source"], importance=row["importance"],
        timestamp=datetime.fromisoformat(row["timestamp"]),
        status=row["status"] if "status" in row.keys() else "active",
        superseded_by=row["superseded_by"] if "superseded_by" in row.keys() else None,
        supersedes=row["supersedes"] if "supersedes" in row.keys() else None,
    )
```

- [ ] **Step 5: Run tests**

```bash
docker compose exec brain pytest brain/tests/test_storage_v2.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 6: Commit**

```bash
git add brain/app/models.py brain/app/storage.py brain/tests/test_storage_v2.py
git commit -m "feat: add status/supersession fields to MemoryEntry and storage layer"
```

---

## Task 3: Update ChromaDB — Status in Metadata

**Files:**
- Modify: `brain/app/chroma.py`

- [ ] **Step 1: Write failing test**

```python
# Add to brain/tests/test_supersession.py (create file, more tests added in Task 4)
import pytest
from unittest.mock import patch, MagicMock
from app.chroma import chroma_add, chroma_update_metadata


def test_chroma_add_includes_status():
    mock_col = MagicMock()
    with patch("app.chroma._get_collection", return_value=mock_col):
        chroma_add("id1", [0.1, 0.2], {"project": "p", "type": "note"})
        call_kwargs = mock_col.upsert.call_args[1]
        assert call_kwargs["metadatas"][0]["status"] == "active"


def test_chroma_update_metadata_archives():
    mock_col = MagicMock()
    with patch("app.chroma._get_collection", return_value=mock_col):
        chroma_update_metadata("id1", {"status": "archived"})
        mock_col.update.assert_called_once_with(ids=["id1"], metadatas=[{"status": "archived"}])
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec brain pytest brain/tests/test_supersession.py -v
```
Expected: `ImportError: cannot import name 'chroma_update_metadata'`

- [ ] **Step 3: Update chroma.py**

```python
# brain/app/chroma.py — replace full file
from pathlib import Path
from typing import Optional
import chromadb

CHROMA_PATH = Path("/app/data/chroma")
COLLECTION_NAME = "memories"


def get_client(path: Path = CHROMA_PATH) -> chromadb.ClientAPI:
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def _get_collection(client: Optional[chromadb.ClientAPI] = None) -> chromadb.Collection:
    if client is None:
        client = get_client()
    return client.get_or_create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def chroma_add(
    memory_id: str,
    embedding: list[float],
    metadata: dict,
    client: Optional[chromadb.ClientAPI] = None,
):
    """Add/upsert a memory embedding. Always includes status='active' unless overridden."""
    col = _get_collection(client)
    full_meta = {"status": "active", **metadata}
    col.upsert(ids=[memory_id], embeddings=[embedding], metadatas=[full_meta])


def chroma_update_metadata(
    memory_id: str,
    metadata: dict,
    client: Optional[chromadb.ClientAPI] = None,
):
    """Partially update metadata fields for an existing entry (e.g. status → archived)."""
    col = _get_collection(client)
    col.update(ids=[memory_id], metadatas=[metadata])


def chroma_search(
    embedding: list[float],
    n_results: int = 20,
    where: Optional[dict] = None,
    client: Optional[chromadb.ClientAPI] = None,
) -> list[dict]:
    col = _get_collection(client)
    count = col.count()
    if count == 0:
        return []
    kwargs: dict = {"query_embeddings": [embedding], "n_results": min(n_results, count)}
    if where:
        kwargs["where"] = where
    results = col.query(**kwargs, include=["metadatas", "distances"])
    if not results["ids"] or not results["ids"][0]:
        return []
    return [
        {"id": id_, "metadata": meta, "distance": dist}
        for id_, meta, dist in zip(
            results["ids"][0], results["metadatas"][0], results["distances"][0]
        )
    ]


def chroma_delete(memory_id: str, client: Optional[chromadb.ClientAPI] = None):
    col = _get_collection(client)
    col.delete(ids=[memory_id])
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec brain pytest brain/tests/test_supersession.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add brain/app/chroma.py brain/tests/test_supersession.py
git commit -m "feat: add status to chroma metadata; add chroma_update_metadata()"
```

---

## Task 4: Supersession Engine

**Files:**
- Modify: `brain/app/ingest_pipeline.py`

- [ ] **Step 1: Add tests to test_supersession.py**

```python
# Append to brain/tests/test_supersession.py
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from app.models import MemoryEntry
from app.ingest_pipeline import _check_supersession, SUPERSESSION_THRESHOLDS


def _entry(type_="note", project="p"):
    return MemoryEntry(content="deploy fix applied to production", type=type_, project=project)


def _make_candidate(distance: float, id_: str = "old-id") -> dict:
    return {"id": id_, "distance": distance, "metadata": {"project": "p", "status": "active"}}


def test_supersession_thresholds_present():
    for t in ["session", "handover", "note", "fact", "file", "reference"]:
        assert t in SUPERSESSION_THRESHOLDS
    assert SUPERSESSION_THRESHOLDS["reference"]["auto"] is None


def test_high_similarity_returns_superseded():
    entry = _entry(type_="note")
    # distance 0.05 → similarity 0.95 → above note auto threshold 0.90
    candidates = [_make_candidate(0.05)]
    mock_get = MagicMock(return_value=MagicMock(summary="old note"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = asyncio.get_event_loop().run_until_complete(
            _check_supersession(entry, [0.1])
        )
    assert "old-id" in superseded
    assert potential == []


def test_medium_similarity_returns_potential():
    entry = _entry(type_="note")
    # distance 0.15 → similarity 0.85 → above warn (0.75) but below auto (0.90)
    candidates = [_make_candidate(0.15)]
    mock_get = MagicMock(return_value=MagicMock(summary="old note"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = asyncio.get_event_loop().run_until_complete(
            _check_supersession(entry, [0.1])
        )
    assert superseded == []
    assert len(potential) == 1
    assert potential[0]["id"] == "old-id"
    assert potential[0]["similarity"] == pytest.approx(0.85, abs=0.01)


def test_low_similarity_returns_nothing():
    entry = _entry(type_="note")
    # distance 0.40 → similarity 0.60 → below warn (0.75)
    candidates = [_make_candidate(0.40)]
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", MagicMock(return_value=None)):
        superseded, potential = asyncio.get_event_loop().run_until_complete(
            _check_supersession(entry, [0.1])
        )
    assert superseded == []
    assert potential == []


def test_reference_type_never_auto_archives():
    entry = _entry(type_="reference")
    # distance 0.01 → similarity 0.99 — very high, but reference never auto-archives
    candidates = [_make_candidate(0.01)]
    mock_get = MagicMock(return_value=MagicMock(summary="ref"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = asyncio.get_event_loop().run_until_complete(
            _check_supersession(entry, [0.1])
        )
    assert superseded == []  # reference: auto is None — never archived
    assert len(potential) == 1  # but warn still fires (0.99 > 0.80)


def test_session_type_lower_threshold():
    entry = _entry(type_="session")
    # distance 0.15 → similarity 0.85 → above session auto threshold 0.80
    candidates = [_make_candidate(0.15)]
    mock_get = MagicMock(return_value=MagicMock(summary="old session"))
    with patch("app.ingest_pipeline.chroma_search", return_value=candidates), \
         patch("app.ingest_pipeline.get_memory", mock_get):
        superseded, potential = asyncio.get_event_loop().run_until_complete(
            _check_supersession(entry, [0.1])
        )
    assert "old-id" in superseded
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec brain pytest brain/tests/test_supersession.py -v
```
Expected: `ImportError: cannot import name '_check_supersession'`

- [ ] **Step 3: Rewrite ingest_pipeline.py**

```python
# brain/app/ingest_pipeline.py
import asyncio
import logging
from typing import Optional
from .models import MemoryEntry, Project, validate_entry
from .storage import add_memory, delete_memory, upsert_project, archive_memory, set_supersedes, get_memory, DB_PATH
from .chroma import chroma_add, chroma_search, chroma_update_metadata
from .summarise import embed, summarise, score_importance

logger = logging.getLogger(__name__)

MAX_CONCURRENT_INGESTS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INGESTS)

SUPERSESSION_THRESHOLDS: dict[str, dict] = {
    "session":   {"auto": 0.80, "warn": 0.70},
    "handover":  {"auto": 0.80, "warn": 0.70},
    "note":      {"auto": 0.90, "warn": 0.75},
    "fact":      {"auto": 0.92, "warn": 0.78},
    "file":      {"auto": 0.85, "warn": 0.72},
    "reference": {"auto": None, "warn": 0.80},
}
_DEFAULT_THRESHOLDS = {"auto": 0.90, "warn": 0.75}


async def _check_supersession(
    entry: MemoryEntry, embedding: list[float]
) -> tuple[list[str], list[dict]]:
    """Scan for similar active memories. Return (superseded_ids, potential_list)."""
    thresholds = SUPERSESSION_THRESHOLDS.get(entry.type, _DEFAULT_THRESHOLDS)
    warn_threshold = thresholds["warn"]
    auto_threshold = thresholds["auto"]  # None for reference

    candidates = chroma_search(
        embedding, n_results=5,
        where={"project": entry.project, "status": "active"},
    )

    superseded: list[str] = []
    potential: list[dict] = []

    for candidate in candidates:
        similarity = round(1.0 - candidate["distance"], 4)
        cid = candidate["id"]

        if auto_threshold is not None and similarity >= auto_threshold:
            superseded.append(cid)
        elif warn_threshold is not None and similarity >= warn_threshold:
            mem = get_memory(cid, db_path=DB_PATH)
            potential.append({
                "id": cid,
                "similarity": similarity,
                "summary": mem.summary if mem else "",
            })

    return superseded, potential


async def ingest(entry: MemoryEntry) -> MemoryEntry:
    """Full ingest pipeline: validate → summarise → score → embed → supersede → store."""
    async with _semaphore:
        return await _ingest_inner(entry)


async def _ingest_inner(entry: MemoryEntry) -> MemoryEntry:
    validate_entry(entry)

    if not entry.summary:
        entry.summary = await summarise(entry.content)
    if entry.importance == 3:
        entry.importance = await score_importance(entry.content)

    embedding = await embed(entry.content)

    # Supersession scan — before writing so we don't compare against ourselves
    superseded_ids, potential = await _check_supersession(entry, embedding)
    entry.superseded = superseded_ids
    entry.potential_supersessions = potential

    # Persist new memory
    add_memory(entry, db_path=DB_PATH)
    try:
        chroma_add(
            memory_id=entry.id,
            embedding=embedding,
            metadata={"project": entry.project, "type": entry.type, "status": "active"},
        )
    except Exception:
        logger.error(f"ChromaDB write failed for {entry.id} — rolling back SQLite entry")
        delete_memory(entry.id, db_path=DB_PATH)
        raise

    # Archive superseded memories (after new one is safely written)
    for old_id in superseded_ids:
        archive_memory(old_id, superseded_by=entry.id, db_path=DB_PATH)
        try:
            chroma_update_metadata(old_id, {"status": "archived"})
        except Exception:
            logger.warning(f"Could not update ChromaDB status for archived {old_id}")

    # Set back-reference on the new memory if it superseded something
    if superseded_ids:
        set_supersedes(entry.id, superseded_ids[0], db_path=DB_PATH)
        entry.supersedes = superseded_ids[0]

    upsert_project(
        Project(slug=entry.project, name=entry.project.replace("-", " ").title()),
        db_path=DB_PATH,
    )
    return entry
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec brain pytest brain/tests/test_supersession.py -v
```
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add brain/app/ingest_pipeline.py brain/tests/test_supersession.py
git commit -m "feat: add semantic supersession engine with type-aware thresholds"
```

---

## Task 5: Search Improvements — Recency Decay + Filters

**Files:**
- Modify: `brain/app/search.py`
- Create: `brain/tests/test_search.py`

- [ ] **Step 1: Write failing tests**

```python
# brain/tests/test_search.py
import pytest
from datetime import datetime, timezone, timedelta
from app.search import reciprocal_rank_fusion, recency_factor


def test_recency_factor_same_day():
    now_iso = datetime.now(timezone.utc).isoformat()
    factor = recency_factor(now_iso, decay_rate=0.02)
    assert factor == pytest.approx(1.0, abs=0.01)


def test_recency_factor_7_days_old():
    ts = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    factor = recency_factor(ts, decay_rate=0.02)
    # 1 / (1 + 7 * 0.02) = 1 / 1.14 ≈ 0.877
    assert factor == pytest.approx(0.877, abs=0.01)


def test_recency_factor_30_days_old():
    ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    factor = recency_factor(ts, decay_rate=0.02)
    # 1 / (1 + 30 * 0.02) = 1 / 1.6 = 0.625
    assert factor == pytest.approx(0.625, abs=0.01)


def test_recency_factor_bad_timestamp_returns_one():
    factor = recency_factor("not-a-date", decay_rate=0.02)
    assert factor == 1.0


def test_rrf_with_decay_boosts_recent():
    recent_ts = datetime.now(timezone.utc).isoformat()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

    kw = [{"id": "old", "timestamp": old_ts}, {"id": "recent", "timestamp": recent_ts}]
    sem = [{"id": "old", "timestamp": old_ts}, {"id": "recent", "timestamp": recent_ts}]

    # Without decay: old ranks first (same RRF score, stable sort preserves order)
    no_decay = reciprocal_rank_fusion(kw, sem, k=60, decay_rate=0.0)
    # With decay: recent should rank higher
    with_decay = reciprocal_rank_fusion(kw, sem, k=60, decay_rate=0.02)
    assert with_decay.index("recent") < with_decay.index("old")
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec brain pytest brain/tests/test_search.py -v
```
Expected: `ImportError: cannot import name 'recency_factor'`

- [ ] **Step 3: Rewrite search.py**

```python
# brain/app/search.py
import os
from datetime import datetime, timezone
from typing import Optional
from .storage import keyword_search, get_memory, DB_PATH
from .chroma import chroma_search
from .summarise import embed

RECENCY_DECAY_RATE = float(os.getenv("RECENCY_DECAY_RATE", "0.02"))


def recency_factor(timestamp_str: str, decay_rate: float) -> float:
    """Returns a score in (0, 1] — 1.0 for today, decaying gently with age."""
    try:
        ts = datetime.fromisoformat(timestamp_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        return 1.0 / (1.0 + max(0.0, days_old) * decay_rate)
    except Exception:
        return 1.0


def reciprocal_rank_fusion(
    keyword_results: list[dict],
    semantic_results: list[dict],
    k: int = 60,
    decay_rate: float = RECENCY_DECAY_RATE,
) -> list[str]:
    scores: dict[str, float] = {}
    ts_map: dict[str, str] = {}

    for rank, item in enumerate(keyword_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
        if "timestamp" in item:
            ts_map[id_] = item["timestamp"]

    for rank, item in enumerate(semantic_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
        if "timestamp" in item:
            ts_map.setdefault(id_, item["timestamp"])

    if decay_rate > 0:
        for id_ in scores:
            if id_ in ts_map:
                scores[id_] *= recency_factor(ts_map[id_], decay_rate)

    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


async def hybrid_search(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
) -> list[dict]:
    kw_results = keyword_search(
        query, limit=20, project=project, type_filter=type_filter,
        days=days, tags=tags, include_history=include_history, db_path=DB_PATH,
    )

    embedding = await embed(query)
    where: dict = {}
    if not include_history:
        where["status"] = "active"
    if project:
        where["project"] = project
    if type_filter:
        where["type"] = type_filter
    sem_results = chroma_search(embedding, n_results=20, where=where or None)

    merged_ids = reciprocal_rank_fusion(kw_results, sem_results)[:limit]

    kw_by_id = {r["id"]: r for r in kw_results}
    output = []
    for id_ in merged_ids:
        if id_ in kw_by_id:
            output.append(kw_by_id[id_])
        else:
            entry = get_memory(id_, db_path=DB_PATH)
            if entry:
                output.append({
                    "id": entry.id, "summary": entry.summary,
                    "type": entry.type, "project": entry.project,
                    "source": entry.source, "importance": entry.importance,
                    "timestamp": entry.timestamp.isoformat(),
                    "status": entry.status,
                })
    return output
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec brain pytest brain/tests/test_search.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add brain/app/search.py brain/tests/test_search.py
git commit -m "feat: add recency decay to RRF, include_history and tag filters to hybrid_search"
```

---

## Task 6: MCP Tool Updates

**Files:**
- Modify: `brain/app/mcp/tools.py`

- [ ] **Step 1: Write failing tests**

```python
# Append to brain/tests/test_main.py (add at end of existing test file)
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_add_memory_description_bypass():
    """description param in add_memory should be used as summary, skipping LLM."""
    mock_entry = AsyncMock()
    mock_entry.id = "abc"
    mock_entry.summary = "My precise description"
    mock_entry.importance = 4
    mock_entry.superseded = []
    mock_entry.potential_supersessions = []

    with patch("app.mcp.tools.ingest", new=AsyncMock(return_value=mock_entry)) as mock_ingest, \
         patch("app.mcp.tools.MemoryEntry") as mock_cls:
        instance = mock_cls.return_value
        instance.summary = ""
        resp = client.post("/messages/", json={...})  # MCP call handled via SSE — test handler directly


def test_handle_add_memory_with_description(tmp_path):
    import asyncio
    from app.mcp.tools import handle_add_memory
    from unittest.mock import AsyncMock, patch, MagicMock

    mock_result = MagicMock()
    mock_result.id = "xyz"
    mock_result.summary = "Custom description used"
    mock_result.importance = 3
    mock_result.superseded = ["old-id"]
    mock_result.potential_supersessions = []

    with patch("app.mcp.tools.ingest", new=AsyncMock(return_value=mock_result)) as mock_ingest:
        result = asyncio.get_event_loop().run_until_complete(
            handle_add_memory(
                content="long content here",
                type="note",
                project="test",
                description="My precise description",
            )
        )
        import json
        data = json.loads(result)
        # Verify ingest was called with summary pre-set
        call_entry = mock_ingest.call_args[0][0]
        assert call_entry.summary == "My precise description"
        # Verify response includes superseded
        assert data["superseded"] == ["old-id"]


def test_handle_delete_memory_success(tmp_path):
    import asyncio
    import json
    from app.mcp.tools import handle_delete_memory
    from app.models import MemoryEntry
    from unittest.mock import patch, MagicMock

    fake_entry = MemoryEntry(content="x", type="note", project="p")
    with patch("app.mcp.tools.get_memory", return_value=fake_entry), \
         patch("app.mcp.tools.delete_memory") as mock_del, \
         patch("app.mcp.tools.chroma_delete") as mock_cdel:
        result = asyncio.get_event_loop().run_until_complete(
            handle_delete_memory(fake_entry.id)
        )
        data = json.loads(result)
        assert data["deleted"] is True
        mock_del.assert_called_once_with(fake_entry.id, db_path=pytest.approx)
        mock_cdel.assert_called_once_with(fake_entry.id)


def test_handle_delete_memory_not_found():
    import asyncio
    import json
    from app.mcp.tools import handle_delete_memory
    with patch("app.mcp.tools.get_memory", return_value=None):
        result = asyncio.get_event_loop().run_until_complete(
            handle_delete_memory("nonexistent")
        )
        data = json.loads(result)
        assert "error" in data
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec brain pytest brain/tests/test_main.py::test_handle_add_memory_with_description brain/tests/test_main.py::test_handle_delete_memory_success brain/tests/test_main.py::test_handle_delete_memory_not_found -v
```
Expected: `ImportError: cannot import name 'handle_delete_memory'`

- [ ] **Step 3: Rewrite mcp/tools.py**

```python
# brain/app/mcp/tools.py
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
```

- [ ] **Step 4: Run tests**

```bash
docker compose exec brain pytest brain/tests/test_main.py -v -k "description or delete_memory"
```
Expected: 3 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
docker compose exec brain pytest brain/tests/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**

```bash
git add brain/app/mcp/tools.py
git commit -m "feat: update MCP tools — description bypass, delete_memory, include_history, tags filter, supersession response"
```

---

## Task 7: Summarise Provider Abstraction (Gemini AI + OpenAI)

**Files:**
- Modify: `brain/app/summarise.py`
- Modify: `brain/requirements.txt`
- Modify: `.env.example`

- [ ] **Step 1: Write failing tests**

```python
# brain/tests/test_summarise.py
import os
import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


def test_ollama_provider_selected_by_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GOOGLE_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
        # Re-import to pick up env state
        import importlib
        import app.summarise as s
        importlib.reload(s)
        provider = s.get_provider()
        assert provider.__class__.__name__ == "OllamaProvider"


def test_gemini_provider_selected_when_key_set():
    with patch.dict(os.environ, {"GOOGLE_API_KEY": "test-key"}):
        import importlib
        import app.summarise as s
        importlib.reload(s)
        with patch("google.generativeai.configure"):
            provider = s.get_provider()
            assert provider.__class__.__name__ == "GeminiProvider"


def test_openai_provider_selected_when_key_set():
    with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
        os.environ.pop("GOOGLE_API_KEY", None)
        import importlib
        import app.summarise as s
        importlib.reload(s)
        provider = s.get_provider()
        assert provider.__class__.__name__ == "OpenAIProvider"


def test_short_content_returns_verbatim_ollama():
    from app.summarise import OllamaProvider
    provider = OllamaProvider.__new__(OllamaProvider)
    # patch _client to avoid actual Ollama call
    short = "short note"
    result = asyncio.get_event_loop().run_until_complete(provider.summarise(short))
    assert result == short  # verbatim — under 400 chars


def test_embed_delegates_to_provider():
    import app.summarise as s
    mock_provider = AsyncMock()
    mock_provider.embed.return_value = [0.1, 0.2, 0.3]
    s._provider = mock_provider
    result = asyncio.get_event_loop().run_until_complete(s.embed("hello"))
    assert result == [0.1, 0.2, 0.3]
    mock_provider.embed.assert_called_once_with("hello")
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec brain pytest brain/tests/test_summarise.py -v
```
Expected: `ImportError: cannot import name 'get_provider'`

- [ ] **Step 3: Rewrite summarise.py**

```python
# brain/app/summarise.py
import os
from abc import ABC, abstractmethod
from typing import Optional
from urllib.parse import urlparse


SHORT_CONTENT_THRESHOLD = 400


class SummariseProvider(ABC):
    @abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    @abstractmethod
    async def summarise(self, content: str, max_sentences: int = 3) -> str: ...

    @abstractmethod
    async def score_importance(self, content: str) -> int: ...

    def _verbatim_if_short(self, content: str) -> Optional[str]:
        return content if len(content) <= SHORT_CONTENT_THRESHOLD else None


class OllamaProvider(SummariseProvider):
    def __init__(self):
        import ollama as _ollama
        url = self._validate_url(os.getenv("OLLAMA_URL", "http://ollama:11434"))
        self._client = _ollama.AsyncClient(host=url)
        self._embed_model = os.getenv("OLLAMA_EMBED_MODEL", "embeddinggemma")
        self._summarise_model = os.getenv("OLLAMA_SUMMARISE_MODEL", "llama3.2:3b")

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"OLLAMA_URL scheme must be http or https, got '{parsed.scheme}'")
        return url

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings(model=self._embed_model, prompt=text)
        return response["embedding"]

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        verbatim = self._verbatim_if_short(content)
        if verbatim is not None:
            return verbatim
        prompt = (
            f"Summarise the following in {max_sentences} sentences. "
            f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
        )
        response = await self._client.generate(model=self._summarise_model, prompt=prompt)
        return response["response"].strip()

    async def score_importance(self, content: str) -> int:
        prompt = (
            "Rate the importance of this note for future reference from 1 to 5. "
            "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
            f"Reply with ONLY the digit:\n\n{content[:500]}"
        )
        response = await self._client.generate(model=self._summarise_model, prompt=prompt)
        try:
            return int(response["response"].strip()[0])
        except (ValueError, IndexError):
            return 3


class GeminiProvider(SummariseProvider):
    def __init__(self):
        import google.generativeai as genai
        genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
        self._genai = genai
        self._embed_model = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")
        self._summarise_model = os.getenv("GEMINI_SUMMARISE_MODEL", "gemini-2.0-flash")

    async def embed(self, text: str) -> list[float]:
        import asyncio
        result = await asyncio.to_thread(
            self._genai.embed_content, model=self._embed_model, content=text
        )
        return result["embedding"]

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        verbatim = self._verbatim_if_short(content)
        if verbatim is not None:
            return verbatim
        import asyncio
        model = self._genai.GenerativeModel(self._summarise_model)
        prompt = (
            f"Summarise the following in {max_sentences} sentences. "
            f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
        )
        response = await asyncio.to_thread(model.generate_content, prompt)
        return response.text.strip()

    async def score_importance(self, content: str) -> int:
        import asyncio
        model = self._genai.GenerativeModel(self._summarise_model)
        prompt = (
            "Rate the importance of this note from 1 to 5. "
            "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
            f"Reply with ONLY the digit:\n\n{content[:500]}"
        )
        response = await asyncio.to_thread(model.generate_content, prompt)
        try:
            return int(response.text.strip()[0])
        except (ValueError, IndexError):
            return 3


class OpenAIProvider(SummariseProvider):
    def __init__(self):
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(
            api_key=os.environ["OPENAI_API_KEY"],
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        )
        self._embed_model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        self._summarise_model = os.getenv("OPENAI_SUMMARISE_MODEL", "gpt-4o-mini")

    async def embed(self, text: str) -> list[float]:
        response = await self._client.embeddings.create(model=self._embed_model, input=text)
        return response.data[0].embedding

    async def summarise(self, content: str, max_sentences: int = 3) -> str:
        verbatim = self._verbatim_if_short(content)
        if verbatim is not None:
            return verbatim
        response = await self._client.chat.completions.create(
            model=self._summarise_model,
            messages=[{
                "role": "user",
                "content": (
                    f"Summarise the following in {max_sentences} sentences. "
                    f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
                ),
            }],
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()

    async def score_importance(self, content: str) -> int:
        response = await self._client.chat.completions.create(
            model=self._summarise_model,
            messages=[{
                "role": "user",
                "content": (
                    "Rate the importance of this note from 1 to 5. "
                    "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
                    f"Reply with ONLY the digit:\n\n{content[:500]}"
                ),
            }],
            max_tokens=1,
        )
        try:
            return int(response.choices[0].message.content.strip()[0])
        except (ValueError, IndexError):
            return 3


def get_provider() -> SummariseProvider:
    """Auto-select provider: Gemini if GOOGLE_API_KEY set, OpenAI if OPENAI_API_KEY set, else Ollama."""
    if os.getenv("GOOGLE_API_KEY"):
        return GeminiProvider()
    if os.getenv("OPENAI_API_KEY"):
        return OpenAIProvider()
    return OllamaProvider()


_provider: Optional[SummariseProvider] = None


def _get_provider() -> SummariseProvider:
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


# Public interface — unchanged so nothing else needs to update
async def embed(text: str) -> list[float]:
    return await _get_provider().embed(text)


async def summarise(content: str, max_sentences: int = 3) -> str:
    return await _get_provider().summarise(content, max_sentences)


async def score_importance(content: str) -> int:
    return await _get_provider().score_importance(content)
```

- [ ] **Step 4: Update requirements.txt**

```
# brain/requirements.txt
fastapi~=0.135.0
uvicorn[standard]~=0.42.0
mcp~=1.26.0
chromadb~=1.5.0
ollama~=0.6.0
httpx~=0.28.0
pydantic~=2.12.0
google-generativeai~=0.8.0
openai~=1.58.0
# test
pytest~=9.0.0
pytest-asyncio~=1.3.0
```

- [ ] **Step 5: Update .env.example**

```bash
# brain/.env.example (full replacement)
# CORE — required
BRAIN_PORT=7741
OLLAMA_URL=http://ollama:11434

# AUTHENTICATION (optional — if unset, all endpoints are open)
BRAIN_API_KEY=

# SEARCH — recency decay (0 = disabled, 0.02 = ~12% decay per week)
RECENCY_DECAY_RATE=0.02

# AI PROVIDER — Ollama is default (no key needed, fully local)
# Set GOOGLE_API_KEY to use Gemini instead of Ollama
GOOGLE_API_KEY=
GEMINI_EMBED_MODEL=models/text-embedding-004
GEMINI_SUMMARISE_MODEL=gemini-2.0-flash

# Set OPENAI_API_KEY to use OpenAI-compatible endpoint instead of Ollama
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_EMBED_MODEL=text-embedding-3-small
OPENAI_SUMMARISE_MODEL=gpt-4o-mini

# Override Ollama model names (only used when neither Gemini nor OpenAI key is set)
OLLAMA_EMBED_MODEL=embeddinggemma
OLLAMA_SUMMARISE_MODEL=llama3.2:3b
```

- [ ] **Step 6: Run tests**

```bash
docker compose exec brain pytest brain/tests/test_summarise.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 7: Rebuild container with new deps and run full suite**

```bash
docker compose up -d --build
docker compose exec brain pytest brain/tests/ -v
```
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add brain/app/summarise.py brain/requirements.txt .env.example
git commit -m "feat: provider abstraction for summarise/embed — Ollama/Gemini/OpenAI auto-selected from env"
```

---

## Task 8: Wire main.py — Migration Runner at Startup

**Files:**
- Modify: `brain/app/main.py`

- [ ] **Step 1: Write failing test**

```python
# Append to brain/tests/test_migrations.py
def test_migration_runner_called_at_startup():
    """main.py lifespan must call run_migrations."""
    import inspect
    import app.main as m
    src = inspect.getsource(m)
    assert "run_migrations" in src, "main.py must call run_migrations in lifespan"
```

- [ ] **Step 2: Run to confirm failure**

```bash
docker compose exec brain pytest brain/tests/test_migrations.py::test_migration_runner_called_at_startup -v
```
Expected: FAIL — `run_migrations` not in main.py yet

- [ ] **Step 3: Update main.py lifespan to call run_migrations**

Find the `lifespan` function (or startup section) in `brain/app/main.py` and add the migration call:

```python
# In brain/app/main.py — add import near top
from .migrations.runner import run_migrations

# In the lifespan context manager, after init_db():
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_migrations(db_path=DB_PATH)   # ← add this line
    yield
```

Full lifespan block after edit:
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    run_migrations(db_path=DB_PATH)
    yield
```

- [ ] **Step 4: Run test**

```bash
docker compose exec brain pytest brain/tests/test_migrations.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add brain/app/main.py
git commit -m "feat: call migration runner at startup — schema updates apply automatically on container start"
```

---

## Task 9: `brain update` CLI Command

**Files:**
- Modify: `cli/brain.py`

- [ ] **Step 1: Write failing test**

```python
# brain/tests/test_cli.py
import sys
import importlib
from pathlib import Path
from unittest.mock import patch, MagicMock


def test_update_command_registered():
    """brain.py must have a cmd_update function."""
    spec = importlib.util.spec_from_file_location("brain_cli", Path("cli/brain.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "cmd_update"), "cli/brain.py must define cmd_update()"


def test_update_fails_gracefully_no_repo(tmp_path):
    """cmd_update must exit cleanly when MEMORYBRAIN_DIR is missing and cwd is not a repo."""
    spec = importlib.util.spec_from_file_location("brain_cli", Path("cli/brain.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    import os
    with patch.dict(os.environ, {}, clear=True):
        os.environ.pop("MEMORYBRAIN_DIR", None)
        with patch("os.getcwd", return_value=str(tmp_path)):
            with patch("sys.exit") as mock_exit:
                mod.cmd_update()
                mock_exit.assert_called_once_with(1)
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /path/to/MemoryBrain && python -m pytest brain/tests/test_cli.py -v
```
Expected: `AttributeError: module has no attribute 'cmd_update'`

- [ ] **Step 3: Add `cmd_update` to cli/brain.py**

Find the section with existing commands in `cli/brain.py` and add:

```python
# Add imports at top of cli/brain.py if not already present
import hashlib
import shutil
import subprocess

# Add function after existing cmd_* functions
def cmd_update():
    """Update MemoryBrain: git pull, rebuild Docker, reinstall hooks and skills."""
    import os
    from pathlib import Path

    # Locate repo directory
    repo_dir = os.getenv("MEMORYBRAIN_DIR")
    if not repo_dir:
        cwd = Path(os.getcwd())
        if (cwd / "brain").exists() and (cwd / "cli").exists():
            repo_dir = str(cwd)
        else:
            print("❌ Cannot find MemoryBrain repo.")
            print("   Set MEMORYBRAIN_DIR env var or run from the repo directory.")
            sys.exit(1)

    repo_path = Path(repo_dir)

    # 1. git pull
    print("⬇️  Pulling latest changes...")
    result = subprocess.run(["git", "pull"], cwd=repo_path, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ git pull failed:\n{result.stderr}")
        sys.exit(1)
    print(result.stdout.strip() or "Already up to date.")

    # 2. Rebuild Docker (migrations run automatically at container startup)
    print("🔨 Rebuilding Docker image...")
    result = subprocess.run(
        ["docker", "compose", "up", "-d", "--build"],
        cwd=repo_path,
    )
    if result.returncode != 0:
        print("❌ Docker rebuild failed.")
        sys.exit(1)
    print("✅ Docker rebuilt — migrations applied automatically at startup.")

    # 3. Reinstall hooks if changed
    hooks_src = repo_path / "hooks"
    hooks_dst = Path.home() / ".claude" / "hooks"
    if hooks_src.exists() and hooks_dst.exists():
        for src in sorted(hooks_src.iterdir()):
            if not src.is_file():
                continue
            dst = hooks_dst / src.name
            if dst.exists():
                src_hash = hashlib.sha256(src.read_bytes()).hexdigest()
                dst_hash = hashlib.sha256(dst.read_bytes()).hexdigest()
                if src_hash == dst_hash:
                    print(f"⏭️  Hook unchanged: {src.name}")
                    continue
            shutil.copy2(src, dst)
            dst.chmod(dst.stat().st_mode | 0o111)
            print(f"✅ Updated hook: {src.name}")

    # 4. Reinstall skills if changed
    skills_src = repo_path / "skills"
    skills_dst = Path.home() / ".claude" / "skills"
    if skills_src.exists():
        for skill_dir in sorted(skills_src.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            dst_dir = skills_dst / skill_dir.name
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst_file = dst_dir / "SKILL.md"
            if dst_file.exists():
                src_hash = hashlib.sha256(skill_file.read_bytes()).hexdigest()
                dst_hash = hashlib.sha256(dst_file.read_bytes()).hexdigest()
                if src_hash == dst_hash:
                    print(f"⏭️  Skill unchanged: {skill_dir.name}")
                    continue
            shutil.copy2(skill_file, dst_file)
            print(f"✅ Updated skill: {skill_dir.name}")

    print("\n✅ MemoryBrain updated successfully.")
    print("   Open a new Claude Code session to use the updated tools.")
```

Also register the command in the `main()` dispatcher at the bottom of `cli/brain.py`. Find the section that matches command strings and add:

```python
elif cmd == "update":
    cmd_update()
```

And add it to the help text:
```python
elif cmd in ("--help", "-h", "help"):
    print("""brain — MemoryBrain CLI
Commands:
  brain add <text>          Store a note from the terminal
  brain import <file>       Import a markdown file
  brain seed                Bulk import MEMORY.md + HANDOVER files from CWD
  brain status              Check brain health
  brain update              Pull latest, rebuild Docker, reinstall hooks/skills
  brain setup [--auto-detect]  Full setup on a new machine
""")
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest brain/tests/test_cli.py -v
```
Expected: 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add cli/brain.py brain/tests/test_cli.py
git commit -m "feat: add 'brain update' command — one-command upgrade on any machine"
```

---

## Task 10: VERSION Bump + Full Suite

**Files:**
- Modify: `VERSION`

- [ ] **Step 1: Bump VERSION**

```bash
echo "0.5.0" > VERSION
```

- [ ] **Step 2: Run full test suite in Docker**

```bash
docker compose up -d --build
docker compose exec brain pytest brain/tests/ -v --tb=short
```
Expected: all tests PASS, no failures

- [ ] **Step 3: Commit and tag**

```bash
git add VERSION
git commit -m "chore: bump version to 0.5.0"
git tag v0.5.0
```

- [ ] **Step 4: Verify migration runs clean on fresh container**

```bash
# Simulate fresh install by clearing data volume, then restart
docker compose down
docker volume rm memorybrain_brain_data 2>/dev/null || true
docker compose up -d
sleep 5
curl http://localhost:7741/health
curl http://localhost:7741/readiness
```
Expected: `{"status":"ok"}` from health, all subsystems green from readiness.

---

## Spec Coverage Self-Check

| Spec section | Covered by task(s) |
|---|---|
| Data model — status/superseded_by/supersedes + migration | Task 1 + Task 2 |
| Semantic supersession engine + type-aware thresholds | Task 3 + Task 4 |
| Archive in response (superseded + potential_supersessions) | Task 4 + Task 6 |
| Search — exclude archived by default, include_history | Task 2 (storage) + Task 5 (search) |
| Search — recency decay on RRF | Task 5 |
| Search — tag filter | Task 2 (storage) + Task 5 (search) |
| Search — type_filter already existed, now plumbed through include_history | Task 5 |
| MCP: updated search_memory (include_history, tags) | Task 6 |
| MCP: updated add_memory (description bypass, supersession response) | Task 6 |
| MCP: new delete_memory | Task 6 |
| MCP: updated get_startup_summary (per-project recent_state) | Task 6 |
| Summarise provider abstraction — Ollama/Gemini/OpenAI | Task 7 |
| .env.example updated | Task 7 |
| Migration runner at startup | Task 8 |
| `brain update` CLI command | Task 9 |
| VERSION 0.5.0 | Task 10 |
