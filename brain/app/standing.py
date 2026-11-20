"""Standing: how much a memory counts today, worked out when it is read.

The stored `importance` is what the writer (or the type's default) gave it.
The importance an agent sees moves with what has happened to the memory since:

  proven     an old fact or decision nobody has corrected. Age never lowers
             it: unchanged for a long time means it held.
  verified   someone checked it and it was still true (verify_memory).
  changed    it replaced an older memory in the last 30 days: the fresh fix.
  disputed   an active memory corrects part of it, or it sits in an open
             contradiction. It drops a step until that is settled, and names
             its correction. Only a step: the rest of it usually still holds,
             and a note that is wrong as a whole is superseded, not disputed.
  stale      an open loop nobody has touched for 30 days.
  superseded, archived, done, proposed: its lifecycle state.
  current, open: nothing special yet.

Effective importance = stored importance
  +1 when reinforced (pinned, written by the user, verified, or recalled often)
  +1 when changed
  -1 when disputed, -1 when stale
kept within 1 to 5. A superseded or archived memory counts 1, a finished loop 2.

Like strength, nothing here is stored: reading twice changes nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import storage as _storage

PROVEN_DAYS = 30
CHANGED_DAYS = 30
STALE_LOOP_DAYS = 30
REINFORCED_STRENGTH = 1.5
STABLE_TYPES = frozenset({"fact", "decision", "reference", "procedure", "belief", "file"})
# search: a disputed memory gives way to its correction on a near tie, and
# no more. Fused scores sit close together, so a bigger cut buried a note
# that was right in everything but one point.
DISPUTED_RANK = 0.93


def _age_days(timestamp: str, now: datetime) -> float:
    try:
        ts = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return 0.0
    ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return max(0.0, (now - ts).total_seconds() / 86400)


def standing_of(row: dict, pinned: bool = False, corrector_active: bool = False,
                in_conflict: bool = False, replaced_something: bool = False,
                now: Optional[datetime] = None) -> dict:
    """The standing of one memory row (id, type, status, importance, timestamp,
    trust, strength, last_recalled, superseded_by, verified_at, corrected_by)."""
    now = now or datetime.now(timezone.utc)
    status, mtype = row.get("status") or "active", row.get("type") or ""
    base = int(row.get("importance") or 3)
    age = _age_days(row.get("timestamp") or "", now)
    out: dict = {"base_importance": base}

    if status == "archived":
        label = "superseded" if row.get("superseded_by") else "archived"
        if row.get("superseded_by"):
            out["superseded_by"] = row["superseded_by"]
        return {**out, "standing": label, "importance": 1}
    if status == "done":
        return {**out, "standing": "done", "importance": min(base, 2)}
    if status == "proposed":
        return {**out, "standing": "proposed", "importance": base}

    strength = _storage.effective_strength(
        row.get("strength"), row.get("timestamp") or "", row.get("last_recalled"),
        pinned, now=now, memory_type=mtype)
    verified = bool(row.get("verified_at"))
    reinforced = (pinned or row.get("trust") == "user" or verified
                  or strength >= REINFORCED_STRENGTH)

    if corrector_active or in_conflict:
        label = "disputed"
        if corrector_active and row.get("corrected_by"):
            out["corrected_by"] = row["corrected_by"]
    elif mtype == "open_loop":
        label = "stale" if age >= STALE_LOOP_DAYS else "open"
    elif verified:
        label = "verified"
        out["verified_at"] = row["verified_at"]
    elif replaced_something and age <= CHANGED_DAYS:
        label = "changed"
    elif mtype in STABLE_TYPES and age >= PROVEN_DAYS:
        label = "proven"
    else:
        label = "current"

    adjust = (1 if reinforced else 0) + (1 if label == "changed" else 0) \
        - (1 if label in ("disputed", "stale") else 0)
    return {**out, "standing": label, "importance": max(1, min(5, base + adjust)),
            "reinforced": reinforced}


_COLUMNS = ("id, type, status, importance, timestamp, trust, strength, last_recalled, "
            "supersedes, superseded_by, verified_at, corrected_by")


def standings(memory_ids: list[str], db_path: Optional[Path] = None) -> dict[str, dict]:
    """standing_of for many memories, in a handful of queries."""
    ids = [i for i in dict.fromkeys(memory_ids or []) if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    now = datetime.now(timezone.utc)
    with _storage._connect(db_path or _storage.DB_PATH) as conn:
        rows = [dict(r) for r in conn.execute(
            f"SELECT {_COLUMNS} FROM memories WHERE id IN ({marks})", ids).fetchall()]
        pinned = {r[0] for r in conn.execute(
            f"SELECT memory_id FROM project_pins WHERE memory_id IN ({marks})", ids)}
        correctors = [r["corrected_by"] for r in rows if r.get("corrected_by")]
        live_correctors = set()
        if correctors:
            live_correctors = {r[0] for r in conn.execute(
                f"""SELECT id FROM memories WHERE status = 'active'
                    AND id IN ({','.join('?' * len(correctors))})""", correctors)}
        conflicted: set = set()
        for src, dst in conn.execute(
                f"""SELECT l.src_id, l.dst_id FROM memory_links l
                    JOIN memories a ON a.id = l.src_id AND a.status = 'active'
                    JOIN memories b ON b.id = l.dst_id AND b.status = 'active'
                    WHERE l.kind = 'conflicts_with' AND l.weight > 0.05
                      AND (l.src_id IN ({marks}) OR l.dst_id IN ({marks}))""", ids + ids):
            conflicted |= {src, dst}
        replacers = {r[0] for r in conn.execute(
            f"SELECT DISTINCT superseded_by FROM memories WHERE superseded_by IN ({marks})", ids)}
    return {
        r["id"]: standing_of(
            r, pinned=r["id"] in pinned,
            corrector_active=r.get("corrected_by") in live_correctors,
            in_conflict=r["id"] in conflicted,
            replaced_something=bool(r.get("supersedes")) or r["id"] in replacers,
            now=now)
        for r in rows
    }


def annotate(items: list[dict], db_path: Optional[Path] = None, id_key: str = "id") -> list[dict]:
    """Give each item (a brief or search row) its standing and its effective
    importance, in place. Items without a known id are left as they are."""
    found = standings([i.get(id_key) for i in items if isinstance(i, dict)], db_path)
    for item in items:
        if not isinstance(item, dict):
            continue
        info = found.get(item.get(id_key))
        if not info:
            continue
        item["standing"] = info["standing"]
        item["importance"] = info["importance"]
        for key in ("corrected_by", "superseded_by", "verified_at"):
            if info.get(key):
                item[key] = info[key]
    return items
