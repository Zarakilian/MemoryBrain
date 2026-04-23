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
            try:
                conn.executescript(mf.read_text(encoding="utf-8"))
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e):
                    raise
                # Schema already at target (init_db created columns ahead of migration) — stamp as applied
            conn.execute(
                "INSERT INTO schema_migrations (filename, applied_at) VALUES (?, ?)",
                (mf.name, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
