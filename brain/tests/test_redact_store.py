"""S3: secrets stored before redaction existed are scrubbed once, after a
backup, and the scrub never runs twice."""
import json

from app.db import connect
from app.models import MemoryEntry
from app.redact_store import SCAN_KEY, scrub_store, scrub_store_once
from app.storage import add_memory, content_hash, get_meta

TOKEN = "ghp_" + "OLDSECRET" + "x" * 27
assert len(TOKEN) == 40


def _seed(db):
    """Rows as a 2.x brain left them: secrets in plain text, written around
    the write path (raw SQL), as redaction never ran on them."""
    entry = MemoryEntry(content="clean", type="note", project="acme")
    add_memory(entry, db_path=db)
    conn = connect(db)
    try:
        conn.execute("UPDATE memories SET content = ?, summary = ?, tags = ?, source = ?, "
                     "content_hash = ? WHERE id = ?",
                     (f"deploy with {TOKEN} today", f"uses {TOKEN}",
                      json.dumps(["t-" + TOKEN]), "src " + TOKEN,
                      content_hash(f"deploy with {TOKEN} today", "acme"), entry.id))
        conn.execute("INSERT INTO memory_audit (id, memory_id, action, actor, reason, at, detail) "
                     "VALUES ('a1', ?, 'archive', 'mcp', ?, '2026-01-01', '{}')",
                     (entry.id, "why " + TOKEN))
        conn.execute("INSERT INTO retrieval_events (id, project, query, result_ids, source, created_at) "
                     "VALUES ('r1', 'acme', ?, '[]', 'mcp', '2026-01-01')", ("find " + TOKEN,))
        conn.execute("INSERT INTO tag_stats (tag, df) VALUES (?, 1)", ("t-" + TOKEN,))
        conn.commit()
    finally:
        conn.close()
    return entry.id


def _anywhere(db, needle) -> list:
    conn = connect(db)
    try:
        hits = []
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
            for col in [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')")]:
                n = conn.execute(f"SELECT COUNT(*) FROM '{table}' WHERE CAST(\"{col}\" AS TEXT) "
                                 "LIKE ?", (f"%{needle}%",)).fetchone()[0]
                if n:
                    hits.append(f"{table}.{col}")
        return hits
    finally:
        conn.close()


def test_the_scrub_clears_every_column_and_requeues_the_vector(tmp_db):
    mid = _seed(tmp_db)
    report = scrub_store(tmp_db)
    assert _anywhere(tmp_db, TOKEN) == []
    assert report["rows"] == 4 and TOKEN not in json.dumps(report)   # one row per seeded table
    conn = connect(tmp_db)
    try:
        row = conn.execute("SELECT content, content_hash, embedded FROM memories WHERE id = ?",
                           (mid,)).fetchone()
        fts = conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH ?",
                           ('"OLDSECRET"',)).fetchone()[0]
    finally:
        conn.close()
    assert "[REDACTED:" in row["content"]
    assert row["content_hash"] == content_hash(row["content"], "acme")
    assert row["embedded"] == 0          # the old vector was made from the secret text
    assert fts == 0


def test_the_scrub_runs_once_and_backs_up_first(tmp_db):
    _seed(tmp_db)
    first = scrub_store_once(tmp_db)
    assert first["rows"] > 0 and first["backup"]
    assert get_meta(SCAN_KEY, db_path=tmp_db)
    backups = sorted((tmp_db.parent / "backups").glob("*.db"))
    assert len(backups) == 1
    assert scrub_store_once(tmp_db) is None
    assert sorted((tmp_db.parent / "backups").glob("*.db")) == backups


def test_a_clean_brain_takes_no_backup(tmp_db):
    add_memory(MemoryEntry(content="nothing secret here", type="note", project="acme"),
               db_path=tmp_db)
    report = scrub_store_once(tmp_db)
    assert report["rows"] == 0 and report["backup"] is None
    assert not list((tmp_db.parent / "backups").glob("*.db"))


def test_the_brain_scrubs_its_store_once_at_startup(tmp_db, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as m
    _seed(tmp_db)
    monkeypatch.setattr(m, "DB_PATH", tmp_db)
    monkeypatch.setattr("app.storage.DB_PATH", tmp_db)
    monkeypatch.setenv("MEMORYBRAIN_REEMBED_RATE", "0")
    m.run_startup_scrub()
    assert _anywhere(tmp_db, TOKEN) == []
