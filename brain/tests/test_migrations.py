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


def test_init_db_calls_run_migrations():
    """init_db must call run_migrations so all schema changes apply automatically."""
    import inspect
    from app.storage import init_db
    src = inspect.getsource(init_db)
    assert "run_migrations" in src, "init_db must call run_migrations"
