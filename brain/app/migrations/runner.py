import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..db import connect

MIGRATIONS_DIR = Path(__file__).parent
BACKUP_KEEP = 5


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_existing_brain(conn: sqlite3.Connection, applied: set[str]) -> bool:
    """True when there is something worth backing up. init_db creates an empty
    memories table before migrating, so an empty table alone means brand new."""
    if applied:
        return True
    has_memories = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memories'").fetchone()
    return bool(has_memories and conn.execute("SELECT 1 FROM memories LIMIT 1").fetchone())


def _backup(conn: sqlite3.Connection, db_path: Path, stem: str) -> Path:
    folder = Path(db_path).parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"brain-pre-{stem}-{_utc_stamp()}.db"
    conn.execute("VACUUM INTO ?", (str(target),))
    copies = sorted(folder.glob("brain-pre-*.db"), key=lambda p: (p.stat().st_mtime, p.name))
    for old in copies[:-BACKUP_KEEP]:
        old.unlink()
    return target


def run_migrations(db_path: Path, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Apply pending *.sql migrations in name order. Idempotent.

    Each file runs in one transaction with its schema_migrations row, so a
    failure leaves nothing from that file behind and the error is raised.
    Before the first pending file of a run, an existing brain is copied to
    backups/ with VACUUM INTO, keeping the newest five copies.
    """
    conn = connect(db_path)
    try:
        # SQLite's rule for schema changes: foreign keys off while altering.
        conn.execute("PRAGMA foreign_keys = OFF")
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
        pending = [mf for mf in sorted(Path(migrations_dir).glob("*.sql"))
                   if mf.name not in applied]
        if not pending:
            return
        if _is_existing_brain(conn, applied):
            _backup(conn, db_path, pending[0].stem)

        for mf in pending:
            try:
                # executescript commits anything pending first, so the
                # transaction has to open inside the script itself.
                conn.executescript("BEGIN IMMEDIATE;\n" + mf.read_text(encoding="utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                    (mf.name, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            except Exception:
                if conn.in_transaction:
                    conn.rollback()
                raise
    finally:
        conn.close()
