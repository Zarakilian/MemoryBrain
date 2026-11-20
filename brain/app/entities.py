"""Named things a memory mentions: hosts, tickets, pull requests, ids, paths,
products and environment variables. Indexed after each write so an agent can
ask "what do we know about db01.internal" or "which memories mention CHG-1177".

v3.3: ticket ids without a hyphen (INC0012345) and pull request refs
(owner/repo#12) are found; field names (UserId, IsActive) are no longer taken
for products; and each install can add its own patterns (entity_patterns), for
the names only that place uses: its servers, its ticket prefixes, its page
ids. Those patterns live in the brain's database, never in the application.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Optional

from . import storage as _storage
from .db import connect
from .workspace.paths import extract_path_tokens

_HOST = re.compile(
    r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+(?:com|net|org|io|local|internal|lan)\b",
    re.IGNORECASE)
_TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d{1,7}\b")
# service-desk ids written without a hyphen: INC0012345, CHG0031234, TASK000123456
_TICKET_PLAIN = re.compile(r"\b([A-Z]{2,6})\d{5,12}\b")
_CVE = re.compile(r"\bCVE-\d{4}-\d{3,7}\b")
_NOT_TICKETS = frozenset({"UTF", "ISO", "SHA", "AES", "RSA", "TLS", "SSL", "HTTP", "HTTPS",
                          "IPV4", "IPV6", "MP3", "MP4", "H264", "X509", "PEP", "RFC",
                          "CVE", "DDR3", "DDR4", "DDR5", "GPT", "RTX", "GTX", "USB", "WIN"})
_PR = re.compile(r"(?<![\w/.-])([A-Za-z0-9][\w.-]{0,38}/[\w.-]{1,100}#\d{1,7})\b")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_PRODUCT = re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}\b")        # CamelCase, two parts or more
# CamelCase that names a field or a call, not a product: UserId, IsActive, GetRowsAsync
_NOT_PRODUCT = re.compile(
    r"^(?:Is|Has|Get|Set|On|To)[A-Z]"
    r"|(?:Id|Ids|Key|Name|Number|Date|Time|Count|Ms|Type|Code|Flag|Status|Url|Path|Size|"
    r"Index|Value|Async|Exception|Request|Response)$")
# UPPER_SNAKE with an underscore, but not the stem of a file name (PROGRESS_LOG.md)
_ENV_VAR = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b(?!\.[A-Za-z0-9]{1,8}\b)")

# ── patterns set per install ───────────────────────────────────────────────
MAX_PATTERNS = 40
MAX_PATTERN_CHARS = 200
MAX_PATTERN_MATCHES = 200
PATTERN_SCAN_CHARS = 50_000
_KIND_RE = re.compile(r"^[a-z][a-z0-9_]{1,19}$")
# a quantified group that itself holds a quantifier ("(a+)+") can take forever
_NESTED_QUANTIFIER = re.compile(r"\([^()]*[+*][^()]*\)\s*(?:[+*]|\{\d)")
BUILT_IN_KINDS = ("host", "ticket", "pr", "uuid", "path", "product", "env_var")


def check_pattern(kind: str, pattern: str) -> re.Pattern:
    """Compile one install pattern, or raise ValueError saying what is wrong.
    A pattern with one capture group names the entity by that group."""
    kind, pattern = (kind or "").strip(), (pattern or "").strip()
    if not _KIND_RE.match(kind):
        raise ValueError("kind must be 2 to 20 lower-case letters, digits or underscores, "
                         "starting with a letter (server, ticket, page ...)")
    if not pattern:
        raise ValueError("pattern must not be empty")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"pattern exceeds {MAX_PATTERN_CHARS} characters")
    if _NESTED_QUANTIFIER.search(pattern):
        raise ValueError("pattern repeats a group that already repeats (such as (a+)+): "
                         "it can take forever on long text")
    try:
        compiled = re.compile(pattern)
    except (re.error, OverflowError, RecursionError) as exc:
        # a count too large to repeat ("a{99999999999}") is an OverflowError, not a re.error
        raise ValueError(f"pattern is not a valid regular expression: {exc}") from None
    if compiled.match(""):
        raise ValueError("pattern matches empty text")
    return compiled


def list_patterns(db_path: Optional[Path] = None) -> list[dict]:
    conn = connect(db_path or _storage.DB_PATH)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT kind, pattern, created_at FROM entity_patterns ORDER BY kind, pattern")]
    except Exception:          # a brain not yet migrated to 3.3
        return []
    finally:
        conn.close()


def add_pattern(kind: str, pattern: str, db_path: Optional[Path] = None) -> dict:
    check_pattern(kind, pattern)
    kind, pattern = kind.strip(), pattern.strip()
    path = db_path or _storage.DB_PATH
    existing = list_patterns(path)
    if any(p["kind"] == kind and p["pattern"] == pattern for p in existing):
        return {"kind": kind, "pattern": pattern, "added": False}
    if len(existing) >= MAX_PATTERNS:
        raise ValueError(f"at most {MAX_PATTERNS} patterns; remove one first")
    conn = connect(path)
    try:
        with conn:
            conn.execute("INSERT INTO entity_patterns (kind, pattern, created_at) VALUES (?, ?, ?)",
                         (kind, pattern, datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()
    return {"kind": kind, "pattern": pattern, "added": True}


def remove_pattern(kind: str, pattern: str, db_path: Optional[Path] = None) -> bool:
    conn = connect(db_path or _storage.DB_PATH)
    try:
        with conn:
            cur = conn.execute("DELETE FROM entity_patterns WHERE kind = ? AND pattern = ?",
                               ((kind or "").strip(), (pattern or "").strip()))
        return bool(cur.rowcount)
    finally:
        conn.close()


def _compiled(db_path: Optional[Path]) -> list[tuple[str, re.Pattern]]:
    out = []
    for row in list_patterns(db_path):
        try:
            out.append((row["kind"], re.compile(row["pattern"])))
        except re.error:
            continue
    return out


def extract_entities(text: str, custom: Optional[list] = None) -> list[tuple[str, str]]:
    """(kind, name) pairs, first appearance order, each once per kind. custom
    is a list of (kind, compiled pattern) pairs: the install's own names."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, name: str) -> None:
        key = (kind, name.lower())
        if name and key not in seen:
            seen.add(key)
            found.append((kind, name))

    text = text or ""
    # hosts are matched word by word: a host name is at most 253 characters,
    # and a long run like "a.a.a.…" would make the pattern backtrack for minutes
    for word in text.split():
        if 4 <= len(word) <= 260:  # a host name is at most 253 characters
            for m in _HOST.finditer(word):
                add("host", m.group(0))
    for m in _CVE.finditer(text):
        add("ticket", m.group(0))
    for pattern in (_TICKET, _TICKET_PLAIN):
        for m in pattern.finditer(text):
            if m.group(1) not in _NOT_TICKETS:
                add("ticket", m.group(0))
    for m in _PR.finditer(text):
        add("pr", m.group(1))
    for m in _UUID.finditer(text):
        add("uuid", m.group(0))
    for kind, pattern in custom or []:
        for m in islice(pattern.finditer(text[:PATTERN_SCAN_CHARS]), MAX_PATTERN_MATCHES):
            add(kind, (m.group(1) if pattern.groups else m.group(0)).strip())
    hosts = {name.lower() for kind, name in found if kind == "host"}
    for token in extract_path_tokens(text):
        if token.lower() not in hosts:
            add("path", token)
    named = {name.lower() for _kind, name in found}
    for m in _PRODUCT.finditer(text):
        word = m.group(0)
        if not _NOT_PRODUCT.search(word) and word.lower() not in named:
            add("product", word)
    for m in _ENV_VAR.finditer(text):
        add("env_var", m.group(0))
    return found


def index_entities(memory_id: str, text: str, db_path: Optional[Path] = None) -> int:
    """Replace this memory's entity mentions. Returns how many entities it names."""
    path = db_path or _storage.DB_PATH
    entities = extract_entities(text, _compiled(path))
    low = (text or "").lower()
    now = datetime.now(timezone.utc).isoformat()
    conn = connect(path)
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


def entity_context(name: str, project: Optional[str] = None, limit: int = 20,
                   db_path: Optional[Path] = None) -> dict:
    """Everything the brain holds about one named thing (a ticket, a server, a
    file, a product): the current memories that mention it, newest first, the
    open loops among them, the files those memories point at, and how much
    history sits behind it. A name the index does not hold falls back to a
    phrase search of the text."""
    path = db_path or _storage.DB_PATH
    wanted = (name or "").strip()
    if not wanted:
        return {"error": "name is required"}
    limit = max(1, min(int(limit), 100))
    conn = connect(path)
    try:
        kinds = [r["kind"] for r in conn.execute(
            "SELECT DISTINCT kind FROM entities WHERE norm = ? ORDER BY kind", (wanted.lower(),))]
        sql = """SELECT DISTINCT m.id FROM entities e
                 JOIN entity_mentions em ON em.entity_id = e.id
                 JOIN memories m ON m.id = em.memory_id
                 WHERE e.norm = ? AND m.status != 'proposed'"""
        params: list = [wanted.lower()]
        if project:
            sql += " AND m.project = ?"
            params.append(project)
        ids = [r["id"] for r in conn.execute(sql, params)]
    finally:
        conn.close()
    how = "index"
    if not ids:
        how = "text"
        phrase = '"' + wanted.replace('"', '""') + '"'
        ids = [r["id"] for r in _storage.keyword_search(
            wanted, limit=200, project=project or None, include_history=True,
            db_path=path, match=phrase)]
    if not ids:
        return {"name": wanted, "kinds": kinds, "found_by": how, "memories": [],
                "open_loops": [], "files": [], "history": 0, "projects": []}
    marks = ",".join("?" * len(ids))
    conn = connect(path)
    try:
        rows = [dict(r) for r in conn.execute(
            f"""SELECT id, type, project, status, summary, timestamp, importance
                FROM memories WHERE id IN ({marks}) ORDER BY timestamp DESC""", ids)]
        files = [dict(r) for r in conn.execute(
            f"""SELECT wf.rel_path, COUNT(*) AS memories FROM file_links fl
                JOIN workspace_files wf ON wf.file_id = fl.dst_id
                WHERE fl.src_kind = 'memory' AND fl.dst_kind = 'file' AND fl.src_id IN ({marks})
                GROUP BY wf.rel_path ORDER BY memories DESC, wf.rel_path LIMIT 10""", ids)]
    finally:
        conn.close()
    live = [r for r in rows if r["status"] == "active"]
    try:
        from .standing import annotate
        annotate(live, path)
    except Exception:
        pass
    stamps = [r["timestamp"] for r in rows]
    return {
        "name": wanted, "kinds": kinds, "found_by": how,
        "projects": sorted({r["project"] for r in rows}),
        "first_seen": min(stamps), "last_seen": max(stamps),
        "memories": [r for r in live if r["type"] != "open_loop"][:limit],
        "open_loops": [r for r in live if r["type"] == "open_loop"][:limit],
        "files": files,
        "history": len(rows) - len(live),   # archived, superseded or finished
    }


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


def reindex_entities(db_path: Optional[Path] = None) -> dict:
    """Index every memory again with today's rules and patterns, and drop the
    entities nothing mentions any more. Run it after changing the patterns, or
    once after upgrading to 3.3 so stored memories gain the new kinds."""
    path = db_path or _storage.DB_PATH
    conn = connect(path)
    try:
        rows = conn.execute("SELECT id, content FROM memories").fetchall()
    finally:
        conn.close()
    for row in rows:
        index_entities(row["id"], row["content"] or "", db_path=path)
    conn = connect(path)
    try:
        with conn:
            conn.execute("""DELETE FROM entities WHERE id NOT IN
                            (SELECT entity_id FROM entity_mentions)""")
        kept = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    finally:
        conn.close()
    return {"memories": len(rows), "entities": kept}
