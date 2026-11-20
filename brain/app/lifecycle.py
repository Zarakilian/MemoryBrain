"""Truth maintenance: a newer memory replaces, corrects or finishes an older one.

Three moves, all audited and reversible (restore_memory):

  supersedes  the old memory is wrong or out of date as a whole. It is
              archived, points at its replacement and stops being current.
  corrects    part of the old memory is wrong. It stays active but is marked
              disputed (corrected_by), so a reader is sent to the correction.
  closes      an open loop is finished. It becomes done.

An agent names the target by its full id or by the first 8 characters, the
way ids are quoted in prose. A prefix that fits more than one memory is
refused. What the user wrote is only ever retired or disputed by the user.

Every target is resolved and checked (plan) before the new memory is stored,
so a bad id fails the whole write instead of leaving half of it behind.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import storage as _storage

MAX_TARGETS = 20
_REF_RE = re.compile(r"^[0-9a-f]{8}[0-9a-f-]{0,28}$")
MOVES = ("supersedes", "closes", "corrects")


class LifecycleError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _short(memory_id: str) -> str:
    return (memory_id or "")[:8]


def resolve_id(ref: str, db_path: Optional[Path] = None) -> str:
    """The full id for a full id or a unique prefix of 8 or more characters."""
    text = (ref or "").strip().lower() if isinstance(ref, str) else ""
    if not _REF_RE.match(text):
        raise LifecycleError(f"{ref!r} is not a memory id: pass the full id or its "
                             "first 8 characters")
    with _storage._connect(db_path or _storage.DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM memories WHERE id = ?", (text,)).fetchone():
            return text
        # only hex digits and hyphens reach here, so the pattern needs no escaping
        rows = conn.execute("SELECT id FROM memories WHERE id LIKE ? LIMIT 2",
                            (text + "%",)).fetchall()
    if not rows:
        raise LifecycleError(f"no memory id starts with {text}")
    if len(rows) > 1:
        raise LifecycleError(f"{text} fits more than one memory: pass the full id")
    return rows[0]["id"]


def _row(memory_id: str, db_path: Path) -> dict:
    with _storage._connect(db_path) as conn:
        row = conn.execute("""SELECT id, type, status, project, trust, summary, supersedes,
                                     corrected_by
                              FROM memories WHERE id = ?""", (memory_id,)).fetchone()
    if row is None:
        raise LifecycleError(f"memory {memory_id} not found")
    return dict(row)


def _check(move: str, row: dict, project: str, trust: str) -> None:
    short = _short(row["id"])
    if project and row["project"] != project:
        raise LifecycleError(f"{short} belongs to project {row['project']}, not {project}")
    if row["status"] == "proposed":
        raise LifecycleError(f"{short} is a proposal waiting for the user's approval")
    if move == "closes":
        if row["type"] != "open_loop":
            raise LifecycleError(f"closes takes open loops; {short} is a {row['type']}. "
                                 "Use supersedes or corrects.")
        return
    if row["trust"] == "user" and trust != "user":
        raise LifecycleError(f"{short} was written by the user: only the user retires or "
                             "disputes it (Atlas)")


def plan(project: str, supersedes=None, closes=None, corrects=None, trust: str = "agent",
         db_path: Optional[Path] = None) -> dict:
    """Resolve and check every target. Returns {"supersedes": [ids], "closes":
    [ids], "corrects": [ids]}; raises LifecycleError naming the first problem."""
    path = db_path or _storage.DB_PATH
    out: dict = {move: [] for move in MOVES}
    seen: dict = {}
    for move, refs in (("supersedes", supersedes), ("closes", closes), ("corrects", corrects)):
        if refs is None:
            continue
        if not isinstance(refs, list):
            raise LifecycleError(f"{move} must be a list of memory ids")
        if len(refs) > MAX_TARGETS:
            raise LifecycleError(f"{move}: at most {MAX_TARGETS} ids, got {len(refs)}")
        for ref in refs:
            memory_id = resolve_id(ref, path)
            if seen.get(memory_id, move) != move:
                raise LifecycleError(f"{_short(memory_id)} is named under both "
                                     f"{seen[memory_id]} and {move}: pick one")
            _check(move, _row(memory_id, path), project, trust)
            if memory_id not in seen:
                seen[memory_id] = move
                out[move].append(memory_id)
    return out


def has_targets(targets: Optional[dict]) -> bool:
    return bool(targets) and any(targets.get(move) for move in MOVES)


def apply(new_id: str, targets: dict, actor: str, db_path: Optional[Path] = None,
          reason: str = "") -> dict:
    """Carry out a plan for the stored memory new_id, in one transaction.
    Returns the ids that changed: {"superseded", "closed", "corrected"}. A
    target that is no longer active is left alone, so applying twice is safe."""
    path = db_path or _storage.DB_PATH
    at = _now()
    done: dict = {"superseded": [], "closed": [], "corrected": []}
    with _storage._connect(path) as conn:
        for old in targets.get("supersedes") or []:
            if old == new_id:
                continue
            cur = conn.execute(
                """UPDATE memories SET status = 'archived', superseded_by = ?, invalidated_by = ?,
                          valid_to = ? WHERE id = ? AND status IN ('active', 'done')""",
                (new_id, new_id, at, old))
            if cur.rowcount:
                _storage._audit(conn, old, "supersede", actor, reason, {"by": new_id})
                done["superseded"].append(old)
        for old in targets.get("closes") or []:
            if old == new_id:
                continue
            cur = conn.execute(
                """UPDATE memories SET status = 'done', valid_to = ?, invalidated_by = ?
                   WHERE id = ? AND status = 'active' AND type = 'open_loop'""",
                (at, new_id or None, old))
            if cur.rowcount:
                _storage._audit(conn, old, "close", actor, reason,
                                {"by": new_id} if new_id else None)
                done["closed"].append(old)
        for old in targets.get("corrects") or []:
            if old == new_id or not new_id:
                continue
            cur = conn.execute(
                "UPDATE memories SET corrected_by = ? WHERE id = ? AND status = 'active'",
                (new_id, old))
            if cur.rowcount:
                _storage._audit(conn, old, "correct", actor, reason, {"by": new_id})
                done["corrected"].append(old)
        if done["superseded"] and new_id:
            conn.execute("UPDATE memories SET supersedes = ? WHERE id = ? AND supersedes IS NULL",
                         (done["superseded"][0], new_id))
        conn.commit()
    for old in done["superseded"]:
        try:  # a no-op on sqlite-vec; keeps the legacy Chroma backend honest
            from .vector import vec_update_metadata
            vec_update_metadata(old, {"status": "archived"})
        except Exception:
            pass
    return done


# ── the same moves for memories that are already stored (brain_admin) ─────

def supersede(old_ref: str, new_ref: str, actor: str, reason: str = "",
              db_path: Optional[Path] = None) -> dict:
    """An existing memory replaces another existing one."""
    path = db_path or _storage.DB_PATH
    new_id = resolve_id(new_ref, path)
    new = _row(new_id, path)
    if new["status"] != "active":
        raise LifecycleError(f"{_short(new_id)} is {new['status']}: only an active memory "
                             "can replace another")
    targets = plan(new["project"], supersedes=[old_ref], db_path=path)
    if targets["supersedes"] == [new_id]:
        raise LifecycleError("a memory cannot replace itself")
    return {"new_id": new_id, **apply(new_id, targets, actor, path, reason)}


def correct(old_ref: str, new_ref: str, actor: str, reason: str = "",
            db_path: Optional[Path] = None) -> dict:
    """An existing memory says part of another one is wrong."""
    path = db_path or _storage.DB_PATH
    new_id = resolve_id(new_ref, path)
    new = _row(new_id, path)
    if new["status"] != "active":
        raise LifecycleError(f"{_short(new_id)} is {new['status']}: only an active memory "
                             "can correct another")
    targets = plan(new["project"], corrects=[old_ref], db_path=path)
    if targets["corrects"] == [new_id]:
        raise LifecycleError("a memory cannot correct itself")
    return {"new_id": new_id, **apply(new_id, targets, actor, path, reason)}


def close_loop(ref: str, actor: str, reason: str = "", by_ref: Optional[str] = None,
               db_path: Optional[Path] = None) -> dict:
    """Mark an open loop done, optionally naming the memory that reports it."""
    path = db_path or _storage.DB_PATH
    loop_id = resolve_id(ref, path)
    loop = _row(loop_id, path)
    by_id = resolve_id(by_ref, path) if by_ref else ""
    targets = plan(loop["project"], closes=[loop_id], db_path=path)
    done = apply(by_id, targets, actor, path, reason)
    return {"id": loop_id, "closed": bool(done["closed"]),
            "status": _row(loop_id, path)["status"]}


def verify(ref: str, actor: str, note: str = "", db_path: Optional[Path] = None) -> dict:
    """Record that someone checked this memory and it is still true."""
    path = db_path or _storage.DB_PATH
    memory_id = resolve_id(ref, path)
    row = _row(memory_id, path)
    if row["status"] != "active":
        raise LifecycleError(f"{_short(memory_id)} is {row['status']}: only an active memory "
                             "can be verified")
    at = _now()
    with _storage._connect(path) as conn:
        conn.execute("UPDATE memories SET verified_at = ?, verified_by = ? WHERE id = ?",
                     (at, actor or "", memory_id))
        _storage._audit(conn, memory_id, "verify", actor, note)
        conn.commit()
    return {"id": memory_id, "verified_at": at, "verified_by": actor}


# ── corrections that were only ever stated in prose ───────────────────────

_STATED_RE = re.compile(
    r"\b(supersedes|superseded|replaces|corrects|closes|correction(?:\s+(?:to|for|of))?)\b"
    r"[^\n]{0,200}", re.IGNORECASE)
_PROSE_ID_RE = re.compile(
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\b", re.IGNORECASE)
_PARTIAL_RE = re.compile(r"\bpart(?:s|ly|ially)?\b|\bauto-summary\b|\bsummary\b", re.IGNORECASE)
STATED_SCAN_CHARS = 600


def stated_corrections(project: Optional[str] = None, limit: int = 50,
                       db_path: Optional[Path] = None) -> dict:
    """Active memories whose opening says they supersede, correct or close
    another memory by id, where that other memory is still active and not yet
    marked as corrected by them. Read-only:
    the caller decides each pair (supersede, correct or close_loop). `partial`
    is a hint that the text speaks of a part or of a summary, not the whole."""
    path = db_path or _storage.DB_PATH
    sql = """SELECT id, project, type, summary, substr(content, 1, ?) AS opening
             FROM memories WHERE status = 'active'
               AND (content LIKE '%upersede%' OR content LIKE '%orrect%'
                    OR content LIKE '%eplaces%' OR content LIKE '%loses%')"""
    params: list = [STATED_SCAN_CHARS]
    if project:
        sql += " AND project = ?"
        params.append(project)
    with _storage._connect(path) as conn:
        rows = conn.execute(sql + " ORDER BY timestamp DESC", params).fetchall()
    pairs: list[dict] = []
    seen: set = set()
    for row in rows:
        for stated in _STATED_RE.finditer(row["opening"] or ""):
            verb = stated.group(1).lower().split()[0]
            for found in _PROSE_ID_RE.finditer(stated.group(0)):
                try:
                    old_id = resolve_id(found.group(0), path)
                    old = _row(old_id, path)
                except LifecycleError:
                    continue
                key = (row["id"], old_id)
                if (old_id == row["id"] or key in seen or old["status"] != "active"
                        or old["project"] != row["project"]
                        or old.get("corrected_by") == row["id"]):   # settled with correct
                    continue
                seen.add(key)
                pairs.append({
                    "new_id": row["id"], "old_id": old_id, "project": row["project"],
                    "says": verb, "old_type": old["type"],
                    "partial": bool(_PARTIAL_RE.search(stated.group(0))),
                    "new_summary": (row["summary"] or "")[:160],
                    "old_summary": (old["summary"] or "")[:160],
                    "text": " ".join(stated.group(0).split())[:200],
                })
    limit = max(1, min(int(limit), 200))
    return {"total": len(pairs), "pairs": pairs[:limit]}
