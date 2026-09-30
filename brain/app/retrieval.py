"""Retrieval telemetry + ranking feedback from chosen results."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from .storage import DB_PATH, _connect

# How strongly past "chosen" clicks boost ranking (0 disables).
import os
FEEDBACK_WEIGHT = float(os.getenv("MEMORYBRAIN_RETRIEVAL_FEEDBACK_WEIGHT", "0.15"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_retrieval(
    query: str,
    result_ids: list[str] | None = None,
    chosen_id: Optional[str] = None,
    project: Optional[str] = None,
    source: str = "",
    db_path: Path = DB_PATH,
) -> dict[str, Any]:
    """Log a search impression and optional click-through for ranking feedback."""
    query = (query or "").strip()
    if not query:
        return {"error": "query is required"}
    result_ids = result_ids or []
    if not isinstance(result_ids, list):
        return {"error": "result_ids must be a list"}
    result_ids = [str(x) for x in result_ids[:50]]
    rid = str(uuid.uuid4())
    with _connect(db_path) as conn:
        conn.execute(
            """INSERT INTO retrieval_events
               (id, project, query, result_ids, chosen_id, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (rid, project or None, query[:500], json.dumps(result_ids),
             chosen_id, (source or "")[:80], _now()),
        )
        conn.commit()
    # chosen_id is a strong recall signal
    if chosen_id:
        try:
            from .storage import record_recall
            record_recall([chosen_id], db_path=db_path)
        except Exception:
            pass
    return {"id": rid, "recorded": True, "chosen_id": chosen_id}


IMPLICIT_CHOICE_WINDOW_S = 600  # reading a result within 10 minutes of the search


def note_implicit_choice(memory_id: str, db_path: Path = DB_PATH) -> Optional[str]:
    """Reading a memory soon after a search that returned it counts as choosing
    it: the newest such event with no chosen_id gets this one. Returns the event
    id, or None. The recall boost comes from the read itself (get_memory)."""
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=IMPLICIT_CHOICE_WINDOW_S)).isoformat()
    with _connect(db_path) as conn:
        row = conn.execute(
            """SELECT id FROM retrieval_events
               WHERE created_at >= ? AND chosen_id IS NULL
                 AND EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(result_ids)
                                                     THEN result_ids ELSE '[]' END)
                             WHERE value = ?)
               ORDER BY created_at DESC LIMIT 1""",
            (cutoff, memory_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE retrieval_events SET chosen_id = ? WHERE id = ? AND chosen_id IS NULL",
                     (memory_id, row["id"]))
        conn.commit()
    return row["id"]


def query_log(limit: int = 500, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """One row per distinct (query, project), newest first, with every chosen id
    as the starting point for hand-labelled relevance."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT query, project, MAX(created_at) AS last_seen, COUNT(*) AS times,
                      json_group_array(chosen_id) AS chosen
               FROM retrieval_events GROUP BY query, project
               ORDER BY last_seen DESC LIMIT ?""",
            (max(1, min(int(limit), 5000)),),
        ).fetchall()
    out = []
    for r in rows:
        chosen = [c for c in json.loads(r["chosen"] or "[]") if c]
        out.append({"query": r["query"], "project": r["project"], "times": r["times"],
                    "last_seen": r["last_seen"], "relevant": sorted(set(chosen))})
    return out


def feedback_boosts(memory_ids: list[str], db_path: Path = DB_PATH,
                    query_terms: Optional[set] = None) -> dict[str, float]:
    """Return multiplicative boosts from historical chosen_id counts.

    Each time a memory was chosen after search, it gets a small ranking lift.
    With query_terms, only choices made after a query that shared at least one
    of those terms count: being picked for "lunch menu" says nothing about
    "printer toner". Bounded so feedback cannot dominate hybrid scores.
    """
    if not memory_ids or FEEDBACK_WEIGHT <= 0:
        return {}
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                f"""SELECT chosen_id, query FROM retrieval_events
                    WHERE chosen_id IN ({','.join('?' * len(memory_ids))})
                      AND chosen_id IS NOT NULL""",
                memory_ids,
            ).fetchall()
        except Exception:
            return {}
    counts: dict[str, int] = {}
    for r in rows:
        if query_terms is not None:
            from .search import query_terms as terms_of
            phrases, words = terms_of(r["query"])
            if not (set(phrases) | set(words)) & set(query_terms):
                continue
        counts[r["chosen_id"]] = counts.get(r["chosen_id"], 0) + 1
    out: dict[str, float] = {}
    for chosen_id, n in counts.items():
        # log-ish: 1 click ~ +FEEDBACK_WEIGHT, 10 clicks ~ +2*FEEDBACK_WEIGHT
        out[chosen_id] = 1.0 + FEEDBACK_WEIGHT * min(3.0, 1.0 + (n - 1) * 0.25)
    return out
