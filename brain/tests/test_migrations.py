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
        from app.migrations.runner import MIGRATIONS_DIR
        assert count == len(list(MIGRATIONS_DIR.glob("*.sql")))


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


def test_migration_008_workspace_tables_and_project_columns():
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "brain.db"
        _make_minimal_db(db)
        run_migrations(db_path=db)
        with sqlite3.connect(db) as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            pcols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        for t in ("workspace_roots", "project_folders", "workspace_files",
                  "file_links", "workspace_touches"):
            assert t in tables, t
        assert {"description", "description_source", "description_updated_at"} <= pcols


def test_migration_008_runs_on_fresh_db_without_projects_table():
    """init_db creates `projects` AFTER run_migrations, so 008 must create it itself."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "brain.db"
        with sqlite3.connect(db) as conn:
            conn.execute("""CREATE TABLE memories (
                id TEXT PRIMARY KEY, content TEXT, summary TEXT DEFAULT '',
                type TEXT, project TEXT, tags TEXT DEFAULT '[]',
                source TEXT DEFAULT '', importance INTEGER DEFAULT 3,
                timestamp TEXT, chroma_id TEXT DEFAULT '', content_hash TEXT DEFAULT '')""")
            conn.commit()
        run_migrations(db_path=db)  # must not raise
        with sqlite3.connect(db) as conn:
            pcols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
        assert "description" in pcols


# ------------------------------------------------------------- v3: atomic runs

import os
import re
import shutil
import time

from app.migrations.runner import MIGRATIONS_DIR


def _mig_dir(tmp_path, files: dict) -> Path:
    d = tmp_path / "migs"
    d.mkdir()
    for name, sql in files.items():
        (d / name).write_text(sql, encoding="utf-8")
    return d


def _backups(db: Path) -> list[Path]:
    return sorted((db.parent / "backups").glob("brain-pre-*.db"))


def test_failed_migration_leaves_nothing_behind(tmp_path):
    db = tmp_path / "brain.db"
    _make_minimal_db(db)
    migs = _mig_dir(tmp_path, {
        "001_bad.sql": "CREATE TABLE made_by_bad (x INTEGER);\nINSERT INTO no_such_table VALUES (1);\n",
    })
    with pytest.raises(sqlite3.OperationalError):
        run_migrations(db_path=db, migrations_dir=migs)
    with sqlite3.connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    assert "made_by_bad" not in tables
    assert rows == 0


def test_existing_brain_gets_one_backup_before_a_pending_migration(tmp_path):
    db = tmp_path / "brain.db"
    _make_minimal_db(db)
    migs = _mig_dir(tmp_path, {"001_first.sql": "CREATE TABLE a (x INTEGER);\n"})
    run_migrations(db_path=db, migrations_dir=migs)
    assert _backups(db) == []  # nothing applied before, no rows: brand new
    (migs / "002_second.sql").write_text("CREATE TABLE b (x INTEGER);\n", encoding="utf-8")
    (migs / "003_third.sql").write_text("CREATE TABLE c (x INTEGER);\n", encoding="utf-8")
    run_migrations(db_path=db, migrations_dir=migs)
    backups = _backups(db)
    assert len(backups) == 1
    assert re.fullmatch(r"brain-pre-002_second-\d{8}T\d{6}Z\.db", backups[0].name)
    with sqlite3.connect(backups[0]) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "a" in tables and "b" not in tables  # the copy is the pre-migration state


def test_brain_with_memories_but_no_migration_history_is_backed_up(tmp_path):
    db = tmp_path / "brain.db"
    _make_minimal_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO memories (id, content, type, project, timestamp) "
                     "VALUES ('m1', 'x', 'note', 'acme', '2026-01-01T00:00:00')")
        conn.commit()
    migs = _mig_dir(tmp_path, {"001_first.sql": "CREATE TABLE a (x INTEGER);\n"})
    run_migrations(db_path=db, migrations_dir=migs)
    assert len(_backups(db)) == 1


def test_fresh_database_gets_no_backup(tmp_path):
    from app.storage import init_db
    db = tmp_path / "fresh" / "brain.db"
    init_db(db)
    assert _backups(db) == []


def test_only_the_newest_five_backups_are_kept(tmp_path):
    db = tmp_path / "brain.db"
    _make_minimal_db(db)
    migs = _mig_dir(tmp_path, {"001_first.sql": "CREATE TABLE a (x INTEGER);\n"})
    run_migrations(db_path=db, migrations_dir=migs)
    bdir = db.parent / "backups"
    bdir.mkdir(exist_ok=True)
    old = []
    for i in range(7):
        p = bdir / f"brain-pre-000_old{i}-20260101T00000{i}Z.db"
        p.write_bytes(b"old")
        stamp = time.time() - 3600 + i * 60
        os.utime(p, (stamp, stamp))
        old.append(p)
    (migs / "002_second.sql").write_text("CREATE TABLE b (x INTEGER);\n", encoding="utf-8")
    run_migrations(db_path=db, migrations_dir=migs)
    left = _backups(db)
    assert len(left) == 5
    assert any(p.name.startswith("brain-pre-002_second-") for p in left)
    assert not any(p.exists() for p in old[:3])
    assert all(p.exists() for p in old[3:])


# ------------------------------------------------------------- v3: schema 009

def _pre_009_db(tmp_path) -> Path:
    """A brain migrated through 008, the state every existing install is in."""
    db = tmp_path / "brain.db"
    _make_minimal_db(db)
    migs = tmp_path / "pre009"
    migs.mkdir()
    for mf in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if mf.name < "009":
            shutil.copy(mf, migs / mf.name)
    run_migrations(db_path=db, migrations_dir=migs)
    return db


def test_009_backfills_trust_validity_and_content_time(tmp_path):
    db = _pre_009_db(tmp_path)
    with sqlite3.connect(db) as conn:
        rows = [
            ("b1", "belief", "agent-x"), ("c1", "note", "consolidation"),
            ("f1", "fact", "claude"), ("n1", "note", "claude"),
        ]
        for mid, mtype, source in rows:
            conn.execute("INSERT INTO memories (id, content, type, project, source, timestamp) "
                         "VALUES (?, 'x', ?, 'acme', ?, '2026-02-03T04:05:06+00:00')",
                         (mid, mtype, source))
        conn.commit()
    run_migrations(db_path=db)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        got = {r["id"]: r for r in conn.execute("SELECT * FROM memories")}
    assert got["b1"]["trust"] == "derived"
    assert got["c1"]["trust"] == "derived"
    assert got["n1"]["trust"] == "agent"
    assert got["f1"]["valid_from"] == "2026-02-03T04:05:06+00:00"
    assert got["n1"]["valid_from"] is None
    assert all(r["content_updated_at"] == "2026-02-03T04:05:06+00:00" for r in got.values())
    assert all(r["embedded"] == 1 and r["writer"] == "" for r in got.values())


def test_009_adds_model_to_vectors_and_new_tables(tmp_db):
    with sqlite3.connect(tmp_db) as conn:
        vcols = {r[1]: r for r in conn.execute("PRAGMA table_info(vec_memories)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "model" in vcols and vcols["model"][4] == "''"
    assert {"vec_chunks", "memory_audit", "entities", "entity_mentions"} <= tables
