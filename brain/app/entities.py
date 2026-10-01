"""Named things a memory mentions: hosts, tickets, ids, paths, products and
environment variables. Indexed after each write so an agent can ask "what do
we know about db01.internal" or "which memories mention CHG-1177"."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import storage as _storage
from .db import connect
from .workspace.paths import extract_path_tokens

_HOST = re.compile(
    r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+(?:com|net|org|io|local|internal|lan)\b",
    re.IGNORECASE)
_TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d{1,7}\b")
_NOT_TICKETS = frozenset({"UTF", "ISO", "SHA", "AES", "RSA", "TLS", "SSL", "HTTP", "HTTPS",
                          "IPV4", "IPV6", "MP3", "MP4", "H264", "X509", "PEP", "RFC"})
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_PRODUCT = re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}\b")        # CamelCase, two parts or more
_ENV_VAR = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")  # UPPER_SNAKE with an underscore


def extract_entities(text: str) -> list[tuple[str, str]]:
    """(kind, name) pairs, first appearance order, each once per kind."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, name: str) -> None:
        key = (kind, name.lower())
        if name and key not in seen:
            seen.add(key)
            found.append((kind, name))

    text = text or ""
    for m in _HOST.finditer(text):
        add("host", m.group(0))
    for m in _TICKET.finditer(text):
        if m.group(1) not in _NOT_TICKETS:
            add("ticket", m.group(0))
    for m in _UUID.finditer(text):
        add("uuid", m.group(0))
    hosts = {name.lower() for kind, name in found if kind == "host"}
    for token in extract_path_tokens(text):
        if token.lower() not in hosts:
            add("path", token)
    for m in _PRODUCT.finditer(text):
        add("product", m.group(0))
    for m in _ENV_VAR.finditer(text):
        add("env_var", m.group(0))
    return found


def index_entities(memory_id: str, text: str, db_path: Optional[Path] = None) -> int:
    """Replace this memory's entity mentions. Returns how many entities it names."""
    entities = extract_entities(text)
    low = (text or "").lower()
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(db_path or _storage.DB_PATH)
    try:
        with conn:
            conn.execute("DELETE FROM entity_mentions WHERE memory_id = ?", (memory_id,))
            for kind, name in entities:
                norm = name.lower()
                row = conn.execute("SELECT id FROM entities WHERE kind = ? AND norm = ?",
                                   (kind, norm)).fetchone()
                entity_id = row["id"] if row else str(uuid.uuid4())
                if row is None:
                    conn.execute("""INSERT INTO entities (id, name, kind, norm, aliases, created_at)
                                    VALUES (?, ?, ?, ?, '[]', ?)""",
                                 (entity_id, name, kind, norm, now))
                conn.execute("""INSERT OR REPLACE INTO entity_mentions (entity_id, memory_id, count)
                                VALUES (?, ?, ?)""",
                             (entity_id, memory_id, max(1, low.count(norm))))
    finally:
        conn.close()
    return len(entities)


def top_entities(project: Optional[str], limit: int = 40,
                 db_path: Optional[Path] = None) -> list[dict]:
    """Most-mentioned entities among active memories (of one project, if given),
    each with its mention count and up to 3 example memory ids."""
    sql = """SELECT e.id, e.name, e.kind, SUM(em.count) AS mentions,
                    COUNT(DISTINCT em.memory_id) AS memories,
                    group_concat(em.memory_id) AS ids
             FROM entity_mentions em
             JOIN entities e ON e.id = em.entity_id
             JOIN memories m ON m.id = em.memory_id
             WHERE m.status = 'active'"""
    params: list = []
    if project:
        sql += " AND m.project = ?"
        params.append(project)
    sql += " GROUP BY e.id ORDER BY mentions DESC, e.name LIMIT ?"
    params.append(max(1, min(int(limit), 200)))
    conn = connect(db_path or _storage.DB_PATH)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [{"name": r["name"], "kind": r["kind"], "mentions": r["mentions"],
             "memories": r["memories"], "memory_ids": (r["ids"] or "").split(",")[:3]}
            for r in rows]


BACKFILL_META = "entities_backfilled_v3"


def backfill_entities(db_path: Optional[Path] = None) -> int:
    """Index every memory stored before entities existed (any 2.x brain), once.
    Returns how many memories were indexed; 0 when it has already run."""
    path = db_path or _storage.DB_PATH
    if _storage.get_meta(BACKFILL_META, db_path=path):
        return 0
    conn = connect(path)
    try:
        rows = conn.execute(
            """SELECT id, summary, content FROM memories m
               WHERE NOT EXISTS (SELECT 1 FROM entity_mentions e WHERE e.memory_id = m.id)"""
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        index_entities(row["id"], f"{row['summary'] or ''}\n{row['content'] or ''}", db_path=path)
    _storage.set_meta(BACKFILL_META, datetime.now(timezone.utc).isoformat(), db_path=path)
    return len(rows)
