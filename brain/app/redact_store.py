"""One-time scrub of secrets stored before write-time redaction existed (S3).

Redaction runs on every write since 3.0, but a brain upgraded from 2.x still
holds whatever its sessions captured. On the first start of a release that
has this module, every text column of every table is run through the same
rules once, after a VACUUM INTO backup (taken only when something will
change). The backup still holds the old text: it is the way back if a rule
ever redacts something it should not have.

Memories whose text changed get a fresh content_hash and embedded=0, so the
re-embed job replaces vectors that were computed from the secret. The full
text index follows the memories table through its triggers.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from .db import connect
from .redact import scrub
from .storage import content_hash, get_meta, set_meta

logger = logging.getLogger(__name__)

SCAN_KEY = "redaction_scan_v1"
# Tables the scrub never touches: vectors (no text), the full text index
# (rebuilt from memories by its triggers) and the brain's own bookkeeping.
_SKIP_TABLES = ("vec_", "memories_fts", "schema_migrations", "brain_meta", "sqlite_")


def _text_columns(conn, table: str) -> tuple[list, list]:
    """(text columns worth scanning, primary key columns). Ids, hashes and
    timestamps are never secret-shaped and are left alone."""
    info = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    pk = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
    cols = [r["name"] for r in info
            if (r["type"] or "").upper() in ("TEXT", "")
            and r["name"] != "id" and not r["name"].endswith(("_id", "_at", "hash"))
            and r["name"] not in ("timestamp", "last_activity", "first_seen", "last_seen")]
    return cols, pk


def _scrubbed(value):
    """The redacted value, or None when nothing changes. JSON lists and
    objects are scrubbed field by field so the result stays valid JSON."""
    if not isinstance(value, str) or not value:
        return None
    if value[:1] in "[{":
        try:
            obj = json.loads(value)
        except ValueError:
            obj = None
        if isinstance(obj, (list, dict)):
            clean = scrub(obj)
            return json.dumps(clean) if clean != obj else None
    clean = scrub(value)
    return clean if clean != value else None


def scrub_store(db_path: Path, dry_run: bool = False) -> dict:
    """Redact every stored text column in one transaction. Returns counts per
    table.column (never values). dry_run counts without writing."""
    report = {"rows": 0, "columns": {}}
    conn = connect(db_path)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")
            if not r[0].startswith(_SKIP_TABLES)]
        changed_memories: set = set()
        conn.execute("BEGIN IMMEDIATE")
        for table in tables:
            cols, pk = _text_columns(conn, table)
            if not cols:
                continue
            key_cols = pk or ["rowid"]
            select = ", ".join(dict.fromkeys(key_cols + cols))
            for row in conn.execute(f"SELECT {select} FROM '{table}'").fetchall():
                updates = {}
                for col in cols:
                    new = _scrubbed(row[col])
                    if new is not None:
                        updates[col] = new
                        name = f"{table}.{col}"
                        report["columns"][name] = report["columns"].get(name, 0) + 1
                if not updates:
                    continue
                report["rows"] += 1
                if dry_run:
                    continue
                sets = ", ".join(f'"{c}" = ?' for c in updates)
                where = " AND ".join(f'"{k}" = ?' if k != "rowid" else "rowid = ?" for k in key_cols)
                conn.execute(f"UPDATE OR REPLACE '{table}' SET {sets} WHERE {where}",
                             (*updates.values(), *(row[k] for k in key_cols)))
                if table == "memories" and "content" in updates:
                    changed_memories.add(row["rowid"] if "rowid" in key_cols else row[key_cols[0]])
        for key in changed_memories:
            mem = conn.execute("SELECT id, content, project FROM memories WHERE id = ?",
                               (key,)).fetchone()
            if mem is not None:
                conn.execute("UPDATE memories SET content_hash = ?, embedded = 0 WHERE id = ?",
                             (content_hash(mem["content"], mem["project"]), mem["id"]))
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise
    finally:
        conn.close()
    return report


def scrub_store_once(db_path: Path) -> Optional[dict]:
    """Run the scrub the first time this release starts, backing up first when
    anything will change. None when it already ran."""
    if get_meta(SCAN_KEY, db_path=db_path):
        return None
    from .migrations.runner import _backup
    found = scrub_store(db_path, dry_run=True)
    backup = None
    if found["rows"]:
        conn = connect(db_path)
        try:
            backup = str(_backup(conn, db_path, "redaction-scan"))
        finally:
            conn.close()
        found = scrub_store(db_path)
        logger.warning("Redaction scan: scrubbed %d stored rows (%s). The pre-scan copy is %s",
                       found["rows"], ", ".join(sorted(found["columns"])), backup)
    set_meta(SCAN_KEY, "done", db_path=db_path)
    return {**found, "backup": backup}
