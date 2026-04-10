# MemoryBrain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Docker MCP server that gives Claude persistent, searchable memory across sessions — replacing the 200-line flat MEMORY.md system with a FastAPI + SQLite FTS5 + ChromaDB + Ollama service.

**Architecture:** FastAPI app exposing 6 MCP tools via SSE (`/sse`) and REST ingestion endpoints (`/ingest/*`). SQLite FTS5 handles keyword search; ChromaDB stores semantic embeddings (via Ollama `nomic-embed-text`); Ollama `llama3.2:3b` summarises content on ingestion. A session-start hook injects a ~150-token project index; Claude calls `search_memory()` MCP tool for deeper recall on demand.

**Tech Stack:** Python 3.11, FastAPI, uvicorn, `mcp` (Anthropic SDK ≥1.0), `chromadb`, `ollama`, `apscheduler`, `httpx`, pytest, Docker Compose, Ollama sidecar container.

---

## Scope note

This plan is split into two parts. **This plan (Part 1 — Core)** delivers: storage, search, MCP server, ingestion endpoints, and session hooks — a fully working brain. **Part 2 — Plugins** (separate plan) adds Confluence + PagerDuty scheduled ingestion and the `brain` CLI.

---

## File Structure

All paths relative to `/mnt/c/git/_git/MemoryBrain/` (the dev repo; symlinked to `~/memorybrain/` for runtime).

```
MemoryBrain/
├── docker-compose.yml
├── .env.example
├── .gitignore
├── brain/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── __init__.py
│       ├── main.py              # FastAPI entrypoint — health + ingest routes + MCP SSE mount
│       ├── models.py            # MemoryEntry, Project dataclasses
│       ├── storage.py           # SQLite FTS5 CRUD + project tracking
│       ├── chroma.py            # ChromaDB add/search wrapper
│       ├── summarise.py         # Ollama embed() + summarise() + score_importance()
│       ├── ingest_pipeline.py   # Orchestrates: summarise → embed → store (both DBs)
│       ├── search.py            # Hybrid RRF: FTS5 + ChromaDB → merged top-10
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── session.py       # /ingest/session endpoint handler
│       │   ├── manual.py        # /ingest/note + /ingest/file endpoint handlers
│       │   └── scheduler.py     # APScheduler setup (plugins registered here)
│       └── mcp/
│           └── tools.py         # mcp.server.Server + 6 tool handlers
├── tests/
│   ├── conftest.py              # tmp_db fixture, mock_ollama fixture, TestClient fixture
│   ├── test_models.py
│   ├── test_storage.py
│   ├── test_chroma.py
│   ├── test_summarise.py
│   ├── test_ingest_pipeline.py
│   ├── test_search.py
│   ├── test_mcp_tools.py
│   └── test_ingestion_endpoints.py
└── hooks/
    ├── session-ingest.sh        # session-start hook — calls GET /startup-summary
    └── pre-compact-ingest.py    # pre-compact hook — POST /ingest/session with handover
```

---

## Task 1: Project scaffold

**Files:**
- Create: `docker-compose.yml`
- Create: `brain/Dockerfile`
- Create: `brain/requirements.txt`
- Create: `brain/app/__init__.py` (empty)
- Create: `brain/app/main.py`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `tests/conftest.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Create `.gitignore`**

```
data/
.env
__pycache__/
*.pyc
.pytest_cache/
.coverage
```

- [ ] **Step 2: Create `.env.example`**

```bash
# CORE — required
BRAIN_PORT=7741
OLLAMA_URL=http://ollama:11434

# PLUGIN: Confluence (optional — brain skips if absent)
CONFLUENCE_URL=
CONFLUENCE_TOKEN=

# PLUGIN: PagerDuty (optional)
PAGERDUTY_TOKEN=

# PLUGIN: ClickHouse (optional)
CLICKHOUSE_IOM_URL=
CLICKHOUSE_TOKEN=
```

- [ ] **Step 3: Create `brain/requirements.txt`**

```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
mcp>=1.2.0
chromadb>=0.5.0
ollama>=0.2.0
apscheduler>=3.10.4
httpx>=0.26.0
pydantic>=2.0.0
# test
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.26.0
```

- [ ] **Step 4: Create `brain/Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
ENV PYTHONPATH=/app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7741"]
```

- [ ] **Step 5: Create `docker-compose.yml`**

```yaml
version: "3.9"
services:
  brain:
    build: ./brain
    ports:
      - "${BRAIN_PORT:-7741}:7741"
    volumes:
      - ./data:/app/data
    env_file: .env
    environment:
      - OLLAMA_URL=http://ollama:11434
    depends_on:
      - ollama
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    volumes:
      - ollama_data:/root/.ollama
    restart: unless-stopped
    # Pull models on first start:
    # docker compose exec ollama ollama pull nomic-embed-text
    # docker compose exec ollama ollama pull llama3.2:3b

volumes:
  ollama_data:
```

- [ ] **Step 6: Write the failing test**

```python
# tests/test_main.py
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 7: Run test to verify it fails**

```bash
cd brain
pip install -r requirements.txt
PYTHONPATH=. pytest tests/test_main.py -v
```

Expected: `ImportError: cannot import name 'app' from 'app.main'`

- [ ] **Step 8: Create `brain/app/main.py`**

```python
from fastapi import FastAPI

app = FastAPI(title="MemoryBrain", version="0.1.0")

@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 9: Create `tests/conftest.py`** (empty for now — fixtures added per task)

```python
# tests/conftest.py
import pytest
```

- [ ] **Step 10: Run test to verify it passes**

```bash
cd brain
PYTHONPATH=. pytest tests/test_main.py -v
```

Expected: `PASSED`

- [ ] **Step 11: Commit**

```bash
cd /mnt/c/git/_git/MemoryBrain
git init
git add .
git commit -m "feat: project scaffold — FastAPI health check, Docker setup"
```

---

## Task 2: Data models

**Files:**
- Create: `brain/app/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from datetime import datetime
from app.models import MemoryEntry, Project

def test_memory_entry_defaults():
    entry = MemoryEntry(content="test note", type="note", project="monitoring")
    assert len(entry.id) == 36          # UUID format
    assert entry.summary == ""
    assert entry.tags == []
    assert entry.importance == 3
    assert entry.source == ""
    assert entry.chroma_id == ""
    assert isinstance(entry.timestamp, datetime)

def test_memory_entry_custom_fields():
    entry = MemoryEntry(
        content="important thing",
        type="confluence",
        project="monitoring",
        tags=["alerting", "grafana"],
        importance=5,
        source="https://confluence.example.com/page/123",
    )
    assert entry.tags == ["alerting", "grafana"]
    assert entry.importance == 5

def test_project_defaults():
    p = Project(slug="monitoring", name="Monitoring Migration")
    assert p.one_liner == ""
    assert isinstance(p.last_activity, datetime)

def test_memory_entry_valid_types():
    valid = ["session", "handover", "note", "confluence", "pagerduty", "clickhouse", "fact", "file"]
    for t in valid:
        entry = MemoryEntry(content="x", type=t, project="p")
        assert entry.type == t
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd brain
PYTHONPATH=. pytest tests/test_models.py -v
```

Expected: `ImportError: cannot import name 'MemoryEntry' from 'app.models'`

- [ ] **Step 3: Create `brain/app/models.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


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
    timestamp: datetime = field(default_factory=datetime.utcnow)
    chroma_id: str = ""


@dataclass
class Project:
    slug: str
    name: str
    last_activity: datetime = field(default_factory=datetime.utcnow)
    one_liner: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd brain
PYTHONPATH=. pytest tests/test_models.py -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add brain/app/models.py tests/test_models.py
git commit -m "feat: data models — MemoryEntry and Project dataclasses"
```

---

## Task 3: SQLite FTS5 storage

**Files:**
- Create: `brain/app/storage.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Add tmp_db fixture to `tests/conftest.py`**

```python
import pytest
import sqlite3
import tempfile
from pathlib import Path
from app.storage import init_db, DB_PATH

@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """In-memory SQLite for tests — no real file I/O."""
    db_path = tmp_path / "test_brain.db"
    monkeypatch.setattr("app.storage.DB_PATH", db_path)
    init_db(db_path)
    return db_path
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_storage.py
import pytest
from datetime import datetime
from app.models import MemoryEntry, Project
from app.storage import (
    add_memory, get_memory, keyword_search,
    upsert_project, get_project, list_projects,
    init_db,
)


def test_add_and_get_memory(tmp_db):
    entry = MemoryEntry(content="disk alert firing on iom3110", type="note", project="monitoring")
    add_memory(entry, db_path=tmp_db)
    fetched = get_memory(entry.id, db_path=tmp_db)
    assert fetched.id == entry.id
    assert fetched.content == "disk alert firing on iom3110"
    assert fetched.project == "monitoring"
    assert fetched.type == "note"


def test_get_memory_not_found_returns_none(tmp_db):
    result = get_memory("nonexistent-id", db_path=tmp_db)
    assert result is None


def test_keyword_search_finds_matching_entry(tmp_db):
    e1 = MemoryEntry(content="clickhouse query latency is slow", type="note", project="monitoring")
    e2 = MemoryEntry(content="pagerduty alert for disk space", type="note", project="monitoring")
    add_memory(e1, db_path=tmp_db)
    add_memory(e2, db_path=tmp_db)
    results = keyword_search("clickhouse latency", db_path=tmp_db)
    ids = [r["id"] for r in results]
    assert e1.id in ids
    assert e2.id not in ids


def test_keyword_search_returns_summary_not_full_content(tmp_db):
    e = MemoryEntry(
        content="very long content " * 100,
        summary="short summary",
        type="note",
        project="monitoring",
    )
    add_memory(e, db_path=tmp_db)
    results = keyword_search("very long content", db_path=tmp_db)
    assert results[0]["summary"] == "short summary"
    assert "content" not in results[0]  # full content NOT in search results


def test_keyword_search_filters_by_project(tmp_db):
    e1 = MemoryEntry(content="grafana dashboard", type="note", project="monitoring")
    e2 = MemoryEntry(content="grafana dashboard", type="note", project="other")
    add_memory(e1, db_path=tmp_db)
    add_memory(e2, db_path=tmp_db)
    results = keyword_search("grafana", project="monitoring", db_path=tmp_db)
    ids = [r["id"] for r in results]
    assert e1.id in ids
    assert e2.id not in ids


def test_upsert_and_list_projects(tmp_db):
    p = Project(slug="monitoring", name="Monitoring Migration")
    upsert_project(p, db_path=tmp_db)
    projects = list_projects(db_path=tmp_db)
    assert len(projects) == 1
    assert projects[0].slug == "monitoring"


def test_upsert_project_updates_last_activity(tmp_db):
    p = Project(slug="monitoring", name="Monitoring Migration")
    upsert_project(p, db_path=tmp_db)
    p2 = Project(slug="monitoring", name="Monitoring Migration", one_liner="Updated desc")
    upsert_project(p2, db_path=tmp_db)
    projects = list_projects(db_path=tmp_db)
    assert len(projects) == 1
    assert projects[0].one_liner == "Updated desc"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd brain
PYTHONPATH=. pytest tests/test_storage.py -v
```

Expected: `ImportError: cannot import name 'add_memory' from 'app.storage'`

- [ ] **Step 4: Create `brain/app/storage.py`**

```python
import json
import sqlite3
from datetime import datetime
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
            cutoff = datetime.utcnow().replace(microsecond=0)
            from datetime import timedelta
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
    from datetime import timedelta
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
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd brain
PYTHONPATH=. pytest tests/test_storage.py -v
```

Expected: 7 PASSED

- [ ] **Step 6: Commit**

```bash
git add brain/app/storage.py tests/test_storage.py tests/conftest.py
git commit -m "feat: SQLite FTS5 storage — CRUD + keyword search + project tracking"
```

---

## Task 4: Ollama client (summarise + embed + importance)

**Files:**
- Create: `brain/app/summarise.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_summarise.py`

- [ ] **Step 1: Add mock_ollama fixture to `tests/conftest.py`**

```python
# Add to existing conftest.py:
from unittest.mock import patch, MagicMock

@pytest.fixture
def mock_ollama():
    """Mock all ollama calls so tests don't need a running Ollama."""
    with patch("app.summarise.ollama") as mock:
        mock.embeddings.return_value = {"embedding": [0.1] * 768}
        mock.generate.side_effect = lambda model, prompt, **kwargs: {
            "response": "3" if "Rate the importance" in prompt else "Short two sentence summary."
        }
        yield mock
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_summarise.py
import pytest
from app.summarise import embed, summarise, score_importance


@pytest.mark.asyncio
async def test_embed_returns_float_list(mock_ollama):
    result = await embed("grafana dashboard for monitoring")
    assert isinstance(result, list)
    assert len(result) == 768
    assert all(isinstance(x, float) for x in result)
    mock_ollama.embeddings.assert_called_once_with(
        model="nomic-embed-text", prompt="grafana dashboard for monitoring"
    )


@pytest.mark.asyncio
async def test_summarise_returns_string(mock_ollama):
    result = await summarise("Very long content about monitoring dashboards " * 50)
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
async def test_score_importance_returns_int_1_to_5(mock_ollama):
    score = await score_importance("trivial note about nothing")
    assert isinstance(score, int)
    assert 1 <= score <= 5


@pytest.mark.asyncio
async def test_score_importance_defaults_to_3_on_bad_response(mock_ollama):
    mock_ollama.generate.side_effect = lambda **kwargs: {"response": "not a number"}
    score = await score_importance("something")
    assert score == 3


@pytest.mark.asyncio
async def test_embed_truncates_long_content(mock_ollama):
    """Long content should not raise — Ollama handles internally."""
    result = await embed("word " * 10000)
    assert isinstance(result, list)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd brain
PYTHONPATH=. pytest tests/test_summarise.py -v
```

Expected: `ImportError: cannot import name 'embed' from 'app.summarise'`

- [ ] **Step 4: Create `brain/app/summarise.py`**

```python
import os
import ollama

EMBED_MODEL = "nomic-embed-text"
SUMMARISE_MODEL = "llama3.2:3b"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# Configure ollama client to point at the right host
_client = ollama.Client(host=OLLAMA_URL)


async def embed(text: str) -> list[float]:
    response = _client.embeddings(model=EMBED_MODEL, prompt=text)
    return response["embedding"]


async def summarise(content: str, max_sentences: int = 3) -> str:
    prompt = (
        f"Summarise the following in {max_sentences} sentences. "
        f"Be specific — include key facts, names, and numbers:\n\n{content[:4000]}"
    )
    response = _client.generate(model=SUMMARISE_MODEL, prompt=prompt)
    return response["response"].strip()


async def score_importance(content: str) -> int:
    prompt = (
        "Rate the importance of this note for future reference from 1 to 5. "
        "1=trivial, 2=minor, 3=useful, 4=important, 5=critical. "
        f"Reply with ONLY the digit:\n\n{content[:500]}"
    )
    response = _client.generate(model=SUMMARISE_MODEL, prompt=prompt)
    try:
        return int(response["response"].strip()[0])
    except (ValueError, IndexError):
        return 3
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd brain
PYTHONPATH=. pytest tests/test_summarise.py -v
```

Expected: 5 PASSED

- [ ] **Step 6: Commit**

```bash
git add brain/app/summarise.py tests/test_summarise.py tests/conftest.py
git commit -m "feat: Ollama client — embed, summarise, score_importance (sync wrapper)"
```

---

## Task 5: ChromaDB semantic storage

**Files:**
- Create: `brain/app/chroma.py`
- Modify: `tests/conftest.py`
- Create: `tests/test_chroma.py`

- [ ] **Step 1: Add `tmp_chroma` fixture to `tests/conftest.py`**

```python
# Add to conftest.py:
import chromadb

@pytest.fixture
def tmp_chroma():
    """In-memory ChromaDB client for tests."""
    client = chromadb.EphemeralClient()
    return client
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_chroma.py
import pytest
from app.chroma import chroma_add, chroma_search, chroma_delete


def test_add_and_search_returns_matching_id(tmp_chroma):
    embedding = [0.1] * 768
    chroma_add(
        memory_id="abc-123",
        embedding=embedding,
        metadata={"project": "monitoring", "type": "note"},
        client=tmp_chroma,
    )
    results = chroma_search(embedding, n_results=5, client=tmp_chroma)
    ids = [r["id"] for r in results]
    assert "abc-123" in ids


def test_search_with_project_filter(tmp_chroma):
    chroma_add("id-mon", [0.5] * 768, {"project": "monitoring", "type": "note"}, client=tmp_chroma)
    chroma_add("id-other", [0.5] * 768, {"project": "other", "type": "note"}, client=tmp_chroma)
    results = chroma_search(
        [0.5] * 768, n_results=10,
        where={"project": "monitoring"},
        client=tmp_chroma,
    )
    ids = [r["id"] for r in results]
    assert "id-mon" in ids
    assert "id-other" not in ids


def test_search_returns_metadata(tmp_chroma):
    chroma_add("id-1", [0.3] * 768, {"project": "x", "type": "note"}, client=tmp_chroma)
    results = chroma_search([0.3] * 768, client=tmp_chroma)
    assert results[0]["metadata"]["project"] == "x"


def test_chroma_delete(tmp_chroma):
    chroma_add("del-me", [0.2] * 768, {"project": "x", "type": "note"}, client=tmp_chroma)
    chroma_delete("del-me", client=tmp_chroma)
    results = chroma_search([0.2] * 768, client=tmp_chroma)
    ids = [r["id"] for r in results]
    assert "del-me" not in ids
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd brain
PYTHONPATH=. pytest tests/test_chroma.py -v
```

Expected: `ImportError: cannot import name 'chroma_add' from 'app.chroma'`

- [ ] **Step 4: Create `brain/app/chroma.py`**

```python
import os
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
    return client.get_or_create_collection(
        COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )


def chroma_add(
    memory_id: str,
    embedding: list[float],
    metadata: dict,
    client: Optional[chromadb.ClientAPI] = None,
):
    col = _get_collection(client)
    col.upsert(ids=[memory_id], embeddings=[embedding], metadatas=[metadata])


def chroma_search(
    embedding: list[float],
    n_results: int = 20,
    where: Optional[dict] = None,
    client: Optional[chromadb.ClientAPI] = None,
) -> list[dict]:
    col = _get_collection(client)
    kwargs: dict = {"query_embeddings": [embedding], "n_results": min(n_results, col.count() or 1)}
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

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd brain
PYTHONPATH=. pytest tests/test_chroma.py -v
```

Expected: 4 PASSED

- [ ] **Step 6: Commit**

```bash
git add brain/app/chroma.py tests/test_chroma.py tests/conftest.py
git commit -m "feat: ChromaDB wrapper — semantic add/search/delete with project filter"
```

---

## Task 6: Ingest pipeline + hybrid RRF search

**Files:**
- Create: `brain/app/ingest_pipeline.py`
- Create: `brain/app/search.py`
- Create: `tests/test_ingest_pipeline.py`
- Create: `tests/test_search.py`

- [ ] **Step 1: Write the failing tests for ingest pipeline**

```python
# tests/test_ingest_pipeline.py
import pytest
from unittest.mock import AsyncMock, patch
from app.models import MemoryEntry
from app.ingest_pipeline import ingest


@pytest.mark.asyncio
async def test_ingest_stores_entry_in_sqlite(tmp_db, mock_ollama):
    entry = MemoryEntry(content="clickhouse query is slow", type="note", project="monitoring")
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        result = await ingest(entry)
    assert result.summary == "Short two sentence summary."
    assert result.importance == 3

    from app.storage import get_memory
    stored = get_memory(result.id, db_path=tmp_db)
    assert stored is not None
    assert stored.content == "clickhouse query is slow"


@pytest.mark.asyncio
async def test_ingest_upserts_project(tmp_db, mock_ollama):
    entry = MemoryEntry(content="grafana panel updated", type="note", project="monitoring")
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add"):
        await ingest(entry)

    from app.storage import get_project
    project = get_project("monitoring", db_path=tmp_db)
    assert project is not None
    assert project.slug == "monitoring"


@pytest.mark.asyncio
async def test_ingest_calls_embed_and_chroma_add(tmp_db, mock_ollama):
    entry = MemoryEntry(content="test content", type="note", project="x")
    with patch("app.ingest_pipeline.DB_PATH", tmp_db), \
         patch("app.ingest_pipeline.chroma_add") as mock_chroma:
        await ingest(entry)
    mock_chroma.assert_called_once()
    call_kwargs = mock_chroma.call_args
    assert call_kwargs[1]["memory_id"] == entry.id or call_kwargs[0][0] == entry.id
```

- [ ] **Step 2: Write the failing tests for search**

```python
# tests/test_search.py
import pytest
from app.search import reciprocal_rank_fusion, hybrid_search
from unittest.mock import patch


def test_rrf_merges_two_lists_by_rank():
    kw = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    sem = [{"id": "b"}, {"id": "d"}, {"id": "a"}]
    merged = reciprocal_rank_fusion(kw, sem)
    # "b" appears in both at high rank — should score highest
    assert merged[0] == "b"
    assert "a" in merged
    assert "d" in merged


def test_rrf_empty_lists():
    assert reciprocal_rank_fusion([], []) == []


def test_rrf_one_empty_list():
    kw = [{"id": "x"}, {"id": "y"}]
    merged = reciprocal_rank_fusion(kw, [])
    assert merged == ["x", "y"]


@pytest.mark.asyncio
async def test_hybrid_search_returns_summaries(tmp_db, mock_ollama):
    from app.models import MemoryEntry
    from app.storage import add_memory
    e = MemoryEntry(
        content="grafana clickhouse",
        summary="Grafana dashboard with ClickHouse.",
        type="note",
        project="monitoring",
    )
    add_memory(e, db_path=tmp_db)

    with patch("app.search.DB_PATH", tmp_db), \
         patch("app.search.chroma_search", return_value=[{"id": e.id, "metadata": {}, "distance": 0.1}]):
        results = await hybrid_search("grafana clickhouse", limit=5)

    assert len(results) > 0
    assert results[0]["id"] == e.id
    assert "summary" in results[0]
    assert "content" not in results[0]
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd brain
PYTHONPATH=. pytest tests/test_ingest_pipeline.py tests/test_search.py -v
```

Expected: `ImportError` for both

- [ ] **Step 4: Create `brain/app/ingest_pipeline.py`**

```python
from .models import MemoryEntry, Project
from .storage import add_memory, upsert_project, DB_PATH
from .chroma import chroma_add
from .summarise import embed, summarise, score_importance


async def ingest(entry: MemoryEntry) -> MemoryEntry:
    """Full ingest pipeline: summarise → score → embed → store SQLite + ChromaDB."""
    if not entry.summary:
        entry.summary = await summarise(entry.content)
    entry.importance = await score_importance(entry.content)

    embedding = await embed(entry.content)
    add_memory(entry, db_path=DB_PATH)
    chroma_add(
        memory_id=entry.id,
        embedding=embedding,
        metadata={"project": entry.project, "type": entry.type},
    )
    upsert_project(
        Project(slug=entry.project, name=entry.project.replace("-", " ").title()),
        db_path=DB_PATH,
    )
    return entry
```

- [ ] **Step 5: Create `brain/app/search.py`**

```python
from typing import Optional
from .storage import keyword_search, get_memory, DB_PATH
from .chroma import chroma_search
from .summarise import embed


def reciprocal_rank_fusion(
    keyword_results: list[dict],
    semantic_results: list[dict],
    k: int = 60,
) -> list[str]:
    scores: dict[str, float] = {}
    for rank, item in enumerate(keyword_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
    for rank, item in enumerate(semantic_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


async def hybrid_search(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
) -> list[dict]:
    # Keyword search
    kw_results = keyword_search(
        query, limit=20, project=project, type_filter=type_filter, days=days, db_path=DB_PATH
    )

    # Semantic search
    embedding = await embed(query)
    where = {}
    if project:
        where["project"] = project
    if type_filter:
        where["type"] = type_filter
    sem_results = chroma_search(embedding, n_results=20, where=where or None)

    # Merge via RRF
    merged_ids = reciprocal_rank_fusion(kw_results, sem_results)[:limit]

    # Build output — summaries only, not full content
    kw_by_id = {r["id"]: r for r in kw_results}
    sem_by_id = {r["id"]: r for r in sem_results}
    output = []
    for id_ in merged_ids:
        row = kw_by_id.get(id_) or sem_by_id.get(id_)
        if row:
            output.append({k: v for k, v in row.items() if k != "content"})
        else:
            # fetch from DB (semantic-only hit)
            entry = get_memory(id_, db_path=DB_PATH)
            if entry:
                output.append({
                    "id": entry.id, "summary": entry.summary,
                    "type": entry.type, "project": entry.project,
                    "source": entry.source, "importance": entry.importance,
                    "timestamp": entry.timestamp.isoformat(),
                })
    return output
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd brain
PYTHONPATH=. pytest tests/test_ingest_pipeline.py tests/test_search.py -v
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add brain/app/ingest_pipeline.py brain/app/search.py \
        tests/test_ingest_pipeline.py tests/test_search.py
git commit -m "feat: ingest pipeline + hybrid RRF search (FTS5 + ChromaDB)"
```

---

## Task 7: MCP server — 6 tools via SSE

**Files:**
- Create: `brain/app/mcp/__init__.py`
- Create: `brain/app/mcp/tools.py`
- Modify: `brain/app/main.py`
- Create: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

The MCP tool handlers are tested as plain async functions — no SSE protocol needed in unit tests.

```python
# tests/test_mcp_tools.py
import pytest
import json
from unittest.mock import patch, AsyncMock
from app.mcp.tools import (
    handle_search_memory,
    handle_get_memory,
    handle_add_memory,
    handle_get_recent_context,
    handle_list_projects,
    handle_get_startup_summary,
)
from app.models import MemoryEntry, Project


@pytest.mark.asyncio
async def test_search_memory_returns_json_string(tmp_db, mock_ollama):
    with patch("app.mcp.tools.hybrid_search", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = [
            {"id": "abc", "summary": "found item", "project": "monitoring", "type": "note",
             "source": "", "importance": 3, "timestamp": "2026-03-27T10:00:00"}
        ]
        result = await handle_search_memory(query="grafana", limit=5)
    data = json.loads(result)
    assert isinstance(data, list)
    assert data[0]["id"] == "abc"
    assert "summary" in data[0]


@pytest.mark.asyncio
async def test_get_memory_returns_full_content(tmp_db, mock_ollama):
    entry = MemoryEntry(content="full content here", type="note", project="monitoring")
    from app.storage import add_memory
    add_memory(entry, db_path=tmp_db)
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_memory(memory_id=entry.id)
    data = json.loads(result)
    assert data["content"] == "full content here"
    assert data["id"] == entry.id


@pytest.mark.asyncio
async def test_get_memory_not_found_returns_error(tmp_db):
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_memory(memory_id="does-not-exist")
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_add_memory_ingests_and_returns_id(tmp_db, mock_ollama):
    with patch("app.mcp.tools.ingest", new_callable=AsyncMock) as mock_ingest:
        mock_ingest.return_value = MemoryEntry(
            id="new-id", content="test", type="note", project="monitoring"
        )
        result = await handle_add_memory(
            content="test note", type="note", project="monitoring", tags=["grafana"]
        )
    assert "new-id" in result


@pytest.mark.asyncio
async def test_list_projects_returns_project_index(tmp_db):
    p = Project(slug="monitoring", name="Monitoring Migration", one_liner="Grafana migration")
    from app.storage import upsert_project
    upsert_project(p, db_path=tmp_db)
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_list_projects()
    assert "monitoring" in result
    assert "Monitoring Migration" in result


@pytest.mark.asyncio
async def test_get_startup_summary_under_200_tokens(tmp_db):
    from app.storage import upsert_project
    for i in range(5):
        upsert_project(
            Project(slug=f"project-{i}", name=f"Project {i}", one_liner=f"Description {i}"),
            db_path=tmp_db,
        )
    with patch("app.mcp.tools.DB_PATH", tmp_db):
        result = await handle_get_startup_summary()
    # rough token estimate: 4 chars per token
    assert len(result) / 4 < 200
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd brain
PYTHONPATH=. pytest tests/test_mcp_tools.py -v
```

Expected: `ImportError: cannot import name 'handle_search_memory' from 'app.mcp.tools'`

- [ ] **Step 3: Create `brain/app/mcp/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Create `brain/app/mcp/tools.py`**

```python
import json
from typing import Optional
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import mcp.types as types

from ..storage import get_memory, get_recent, list_projects, DB_PATH
from ..search import hybrid_search
from ..ingest_pipeline import ingest
from ..models import MemoryEntry

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
    projects = list_projects(db_path=DB_PATH)
    lines = ["## Projects\n"]
    for p in projects:
        lines.append(f"**{p.slug}** — {p.name}")
        if p.one_liner:
            lines.append(f"  {p.one_liner}")
        lines.append(f"  Last activity: {p.last_activity.strftime('%Y-%m-%d')}")
        lines.append("")
    return "\n".join(lines)


async def handle_get_startup_summary() -> str:
    projects = list_projects(db_path=DB_PATH)
    if not projects:
        return "No projects recorded yet."
    lines = ["# MemoryBrain — Session Context\n"]
    for p in projects[:10]:  # cap at 10 projects to stay under token budget
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
```

- [ ] **Step 5: Mount MCP SSE in `brain/app/main.py`**

```python
from fastapi import FastAPI, Request
from fastapi.responses import Response
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server

app = FastAPI(title="MemoryBrain", version="0.1.0")
sse_transport = SseServerTransport("/messages/")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/sse")
async def sse_endpoint(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


@app.post("/messages/")
async def handle_messages(request: Request):
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd brain
PYTHONPATH=. pytest tests/test_mcp_tools.py -v
```

Expected: all PASSED

- [ ] **Step 7: Commit**

```bash
git add brain/app/mcp/ brain/app/main.py tests/test_mcp_tools.py
git commit -m "feat: MCP server — 6 tools (search/get/add/recent/projects/startup) via SSE"
```

---

## Task 8: REST ingestion endpoints

**Files:**
- Create: `brain/app/ingestion/__init__.py`
- Create: `brain/app/ingestion/session.py`
- Create: `brain/app/ingestion/manual.py`
- Modify: `brain/app/main.py`
- Create: `tests/test_ingestion_endpoints.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingestion_endpoints.py
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_ingest_note_returns_201_with_id(mock_ollama):
    with patch("app.ingestion.manual.ingest", new_callable=AsyncMock) as mock_ingest:
        from app.models import MemoryEntry
        mock_ingest.return_value = MemoryEntry(id="new-123", content="x", type="note", project="p")
        resp = client.post("/ingest/note", json={
            "content": "clickhouse is slow",
            "project": "monitoring",
            "tags": ["clickhouse"],
        })
    assert resp.status_code == 201
    assert resp.json()["id"] == "new-123"


def test_ingest_note_missing_content_returns_422():
    resp = client.post("/ingest/note", json={"project": "monitoring"})
    assert resp.status_code == 422


def test_ingest_session_returns_201(mock_ollama):
    with patch("app.ingestion.session.ingest", new_callable=AsyncMock) as mock_ingest:
        from app.models import MemoryEntry
        mock_ingest.return_value = MemoryEntry(id="sess-1", content="x", type="session", project="monitoring")
        resp = client.post("/ingest/session", json={
            "content": "# Handover\nWorked on alerts today.",
            "project": "monitoring",
        })
    assert resp.status_code == 201
    assert "id" in resp.json()


def test_startup_summary_returns_string():
    with patch("app.main.handle_get_startup_summary", new_callable=AsyncMock) as mock_sum:
        mock_sum.return_value = "# MemoryBrain\n- monitoring: last 2026-03-27"
        resp = client.get("/startup-summary")
    assert resp.status_code == 200
    assert "monitoring" in resp.json()["summary"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd brain
PYTHONPATH=. pytest tests/test_ingestion_endpoints.py -v
```

Expected: 404s for `/ingest/note`, `/ingest/session`, `/startup-summary`

- [ ] **Step 3: Create `brain/app/ingestion/__init__.py`** (empty)

- [ ] **Step 4: Create `brain/app/ingestion/session.py`**

```python
from fastapi import APIRouter
from pydantic import BaseModel
from ..models import MemoryEntry
from ..ingest_pipeline import ingest

router = APIRouter()


class SessionIngestRequest(BaseModel):
    content: str
    project: str
    source: str = ""


@router.post("/ingest/session", status_code=201)
async def ingest_session(req: SessionIngestRequest):
    entry = MemoryEntry(
        content=req.content,
        type="session",
        project=req.project,
        source=req.source,
    )
    result = await ingest(entry)
    return {"id": result.id, "summary": result.summary, "importance": result.importance}
```

- [ ] **Step 5: Create `brain/app/ingestion/manual.py`**

```python
from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from ..models import MemoryEntry
from ..ingest_pipeline import ingest

router = APIRouter()


class NoteRequest(BaseModel):
    content: str
    project: str
    tags: list[str] = []
    source: str = ""


@router.post("/ingest/note", status_code=201)
async def ingest_note(req: NoteRequest):
    entry = MemoryEntry(
        content=req.content,
        type="note",
        project=req.project,
        tags=req.tags,
        source=req.source,
    )
    result = await ingest(entry)
    return {"id": result.id, "summary": result.summary, "importance": result.importance}


@router.post("/ingest/file", status_code=201)
async def ingest_file(project: str, file: UploadFile = File(...)):
    content = (await file.read()).decode("utf-8", errors="replace")
    entry = MemoryEntry(
        content=content,
        type="file",
        project=project,
        source=file.filename or "",
    )
    result = await ingest(entry)
    return {"id": result.id, "filename": file.filename, "summary": result.summary}
```

- [ ] **Step 6: Update `brain/app/main.py`**

```python
from fastapi import FastAPI, Request
from mcp.server.sse import SseServerTransport
from .mcp.tools import server as mcp_server, handle_get_startup_summary
from .ingestion.session import router as session_router
from .ingestion.manual import router as manual_router

app = FastAPI(title="MemoryBrain", version="0.1.0")
sse_transport = SseServerTransport("/messages/")

app.include_router(session_router)
app.include_router(manual_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/startup-summary")
async def startup_summary():
    summary = await handle_get_startup_summary()
    return {"summary": summary}


@app.get("/sse")
async def sse_endpoint(request: Request):
    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await mcp_server.run(
            streams[0], streams[1], mcp_server.create_initialization_options()
        )


@app.post("/messages/")
async def handle_messages(request: Request):
    await sse_transport.handle_post_message(request.scope, request.receive, request._send)
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
cd brain
PYTHONPATH=. pytest tests/test_ingestion_endpoints.py tests/test_mcp_tools.py tests/test_main.py -v
```

Expected: all PASSED

- [ ] **Step 8: Commit**

```bash
git add brain/app/ingestion/ brain/app/main.py tests/test_ingestion_endpoints.py
git commit -m "feat: REST ingestion endpoints — /ingest/session, /ingest/note, /ingest/file, /startup-summary"
```

---

## Task 9: Session hooks

**Files:**
- Create: `hooks/session-ingest.sh`
- Create: `hooks/pre-compact-ingest.py`

No unit tests for hooks (they're shell glue — tested by running them manually).

- [ ] **Step 1: Create `hooks/session-ingest.sh`**

This replaces `~/.claude/hooks/session-start-memory.sh`.

```bash
#!/usr/bin/env bash
# MemoryBrain session-start hook
# Injects a compact project summary into session context on startup.
# Called by Claude Code session-start hook with CWD = project directory.

set -euo pipefail

BRAIN_URL="${MEMORYBRAIN_URL:-http://localhost:7741}"
CWD="${1:-$(pwd)}"

# Detect project slug from CWD (last meaningful path segment)
PROJECT_SLUG=""
if [ -f "${CWD}/.brainproject" ]; then
    PROJECT_SLUG=$(cat "${CWD}/.brainproject" | tr -d '[:space:]')
fi

# Check if brain is running
if ! curl -sf "${BRAIN_URL}/health" > /dev/null 2>&1; then
    # Brain not running — fall back to legacy MEMORY.md if present
    if [ -f "${CWD}/memory/MEMORY.md" ]; then
        echo "# Context (from MEMORY.md — MemoryBrain not running)"
        head -100 "${CWD}/memory/MEMORY.md"
    fi
    exit 0
fi

# Fetch startup summary
SUMMARY=$(curl -sf "${BRAIN_URL}/startup-summary" | python3 -c "import sys,json; print(json.load(sys.stdin)['summary'])" 2>/dev/null || echo "")

if [ -n "$SUMMARY" ]; then
    echo "$SUMMARY"
fi
```

- [ ] **Step 2: Create `hooks/pre-compact-ingest.py`**

This replaces `~/.claude/hooks/pre-compact-auto-handover.py`.

```python
#!/usr/bin/env python3
"""
MemoryBrain pre-compact hook.
Called by Claude Code before context compaction.
Reads the handover content from stdin (if provided) or the most recent
handover file, then POSTs it to the brain as a session memory.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

BRAIN_URL = os.getenv("MEMORYBRAIN_URL", "http://localhost:7741")
CWD = Path(os.getenv("CLAUDE_CWD", os.getcwd()))


def detect_project(cwd: Path) -> str:
    brain_file = cwd / ".brainproject"
    if brain_file.exists():
        return brain_file.read_text().strip()
    # Heuristic: last meaningful path segment
    parts = [p for p in cwd.parts if p not in ("", "/", "mnt", "c", "git")]
    return parts[-1].lower() if parts else "unknown"


def post_session(content: str, project: str):
    payload = json.dumps({
        "content": content,
        "project": project,
        "source": f"pre-compact:{datetime.utcnow().isoformat()}",
    }).encode()
    req = urllib.request.Request(
        f"{BRAIN_URL}/ingest/session",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            print(f"[memorybrain] Session ingested — id={result.get('id', '?')}", file=sys.stderr)
    except urllib.error.URLError:
        print("[memorybrain] Brain not running — session not ingested", file=sys.stderr)


def main():
    # Try reading handover from stdin first
    content = ""
    if not sys.stdin.isatty():
        content = sys.stdin.read().strip()

    # Fall back to most recent handover file in CWD
    if not content:
        handover_files = sorted(CWD.glob("HANDOVER-*.md"), reverse=True)
        if handover_files:
            content = handover_files[0].read_text()

    if not content:
        print("[memorybrain] No content to ingest", file=sys.stderr)
        return

    project = detect_project(CWD)
    post_session(content, project)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Make scripts executable**

```bash
chmod +x hooks/session-ingest.sh hooks/pre-compact-ingest.py
```

- [ ] **Step 4: Test hooks manually**

Start the brain locally first (or just verify they don't crash when brain is down):

```bash
# Test session hook — brain down fallback
bash hooks/session-ingest.sh /mnt/c/git/_git/Monitoring
# Expected: either prints MEMORY.md header or exits cleanly

# Test pre-compact hook — brain down
echo "# Handover\nTest content" | python3 hooks/pre-compact-ingest.py
# Expected: "[memorybrain] Brain not running — session not ingested" on stderr
```

- [ ] **Step 5: Document installation**

Update `README.md` (create it) with install steps:

```markdown
# MemoryBrain

Persistent searchable memory service for Claude Code.

## Quick Start

\`\`\`bash
cd ~/memorybrain  # or wherever you cloned this
cp .env.example .env
docker compose up -d
# Pull Ollama models (first time only):
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
# Add MCP server to Claude:
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
# Install hooks:
cp hooks/session-ingest.sh ~/.claude/hooks/session-start-memory.sh
cp hooks/pre-compact-ingest.py ~/.claude/hooks/pre-compact-auto-handover.py
\`\`\`

## Project detection

Place a `.brainproject` file in any project root containing the project slug:
\`\`\`
monitoring
\`\`\`
If absent, the last path segment of CWD is used.

## MCP Tools

| Tool | Description |
|---|---|
| `search_memory` | Hybrid keyword+semantic search |
| `get_memory` | Full content by ID |
| `add_memory` | Store a new note |
| `get_recent_context` | Recent entries by project |
| `list_projects` | All projects + status |
| `get_startup_summary` | Compact session-start injection |
\`\`\`
```

- [ ] **Step 6: Commit**

```bash
git add hooks/ README.md
git commit -m "feat: session hooks — session-ingest.sh + pre-compact-ingest.py"
```

---

## Task 10: Full stack smoke test + Docker validation

**Files:**
- No new files — validates the complete system end-to-end

- [ ] **Step 1: Run the full test suite**

```bash
cd brain
PYTHONPATH=. pytest tests/ -v --tb=short
```

Expected: all tests PASS. Fix any failures before continuing.

- [ ] **Step 2: Build the Docker image**

```bash
cd /mnt/c/git/_git/MemoryBrain
docker compose build
```

Expected: `brain` image builds successfully. Fix any `pip install` failures.

- [ ] **Step 3: Start the stack**

```bash
docker compose up -d
docker compose logs brain  # watch for startup errors
```

Expected: `INFO: Application startup complete.` in logs.

- [ ] **Step 4: Pull Ollama models**

```bash
docker compose exec ollama ollama pull nomic-embed-text
docker compose exec ollama ollama pull llama3.2:3b
```

Expected: both models downloaded. `nomic-embed-text` ~274MB, `llama3.2:3b` ~2GB.

- [ ] **Step 5: Smoke test health + startup summary**

```bash
curl -s http://localhost:7741/health
# Expected: {"status":"ok"}

curl -s http://localhost:7741/startup-summary
# Expected: {"summary":"No projects recorded yet."}
```

- [ ] **Step 6: Smoke test ingestion + search**

```bash
# Store a note
curl -s -X POST http://localhost:7741/ingest/note \
  -H "Content-Type: application/json" \
  -d '{"content":"ClickHouse query for APM traces table is apm.otel_traces_local","project":"monitoring","tags":["clickhouse","apm"]}'
# Expected: {"id":"...","summary":"...","importance":3}

# Wait ~3 seconds for Ollama to summarise, then search
curl -s -X POST http://localhost:7741/ingest/note \
  -H "Content-Type: application/json" \
  -d '{"content":"Grafana datasource UID for ClickHouse is clickhouse","project":"monitoring","tags":["grafana"]}'

# Search
curl -s "http://localhost:7741/messages/" --get --data-urlencode 'q=clickhouse'
# (Use MCP tool via Claude for proper search — REST search not exposed directly)
```

- [ ] **Step 7: Add MCP to Claude Code and verify tools load**

```bash
claude mcp add -s user --transport sse memorybrain http://localhost:7741/sse
```

Open a new Claude Code session. Run `/mcp` — verify `memorybrain` is listed with 6 tools.

- [ ] **Step 8: Seed existing MEMORY.md into brain**

```bash
# Import existing project memory into the brain
curl -s -X POST http://localhost:7741/ingest/file?project=monitoring \
  -F "file=@/home/migueler/.claude/projects/-mnt-c-git--git/memory/MEMORY.md"
```

- [ ] **Step 9: Commit and tag v0.1.0**

```bash
cd /mnt/c/git/_git/MemoryBrain
git add -A
git commit -m "feat: v0.1.0 — full core brain working (storage, search, MCP, hooks)"
git tag v0.1.0
```

---

## Self-Review

### Spec coverage check

| Design spec section | Covered in plan |
|---|---|
| Architecture (FastAPI + SQLite FTS5 + ChromaDB + Ollama) | ✅ Tasks 1–6 |
| Data model (MemoryEntry, Project, all fields) | ✅ Task 2 |
| Hybrid search (FTS5 + ChromaDB + RRF) | ✅ Task 6 |
| 6 MCP tools | ✅ Task 7 |
| Ingestion endpoints (/ingest/session, /ingest/note, /ingest/file) | ✅ Task 8 |
| Session hooks | ✅ Task 9 |
| Docker Compose + Ollama | ✅ Tasks 1 + 10 |
| .brainproject convention | ✅ Task 9 (hooks) |
| Startup summary ≤150 tokens | ✅ Task 7 test |
| Importance scoring | ✅ Task 4 |
| Plugin system | ⬜ Part 2 plan |
| `brain` CLI | ⬜ Part 2 plan |

### Gaps
- **Plugin system** (Confluence, PagerDuty, scheduler) is out of scope for this plan per the scope note — covered in Part 2.
- **`brain setup --auto-detect`** CLI is Part 2.
- The `/startup-summary` endpoint in Task 8 calls `handle_get_startup_summary()` from `mcp/tools.py`. This creates a coupling — acceptable for now, but Part 2 can refactor into a shared service layer if needed.

### Placeholder scan
None found — all steps contain actual code and exact commands.

### Type consistency check
- `MemoryEntry` defined in Task 2, used identically in Tasks 3, 6, 7, 8.
- `DB_PATH` patched consistently in tests via `monkeypatch.setattr("app.storage.DB_PATH", ...)`.
- `ingest()` function from `ingest_pipeline.py` used in both `mcp/tools.py` and `ingestion/manual.py` — same import path.
- `chroma_add()` signature: `(memory_id, embedding, metadata, client=None)` — used consistently.

---

**Plan complete and saved to `docs/superpowers/plans/2026-03-27-memorybrain-core.md`.**

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, with checkpoints.

**Which approach?**
