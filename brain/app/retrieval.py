"""Retrieval telemetry + ranking feedback from chosen results."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
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


def feedback_boosts(memory_ids: list[str],
                    db_path: Path = DB_PATH) -> dict[str, float]:
    """Return multiplicative boosts from historical chosen_id counts.

    Each time a memory was chosen after search, it gets a small ranking lift.
    Bounded so feedback cannot dominate hybrid scores.
    """
    if not memory_ids or FEEDBACK_WEIGHT <= 0:
        return {}
    with _connect(db_path) as conn:
        try:
            rows = conn.execute(
                f"""SELECT chosen_id, COUNT(*) AS n
                    FROM retrieval_events
                    WHERE chosen_id IN ({','.join('?' * len(memory_ids))})
                      AND chosen_id IS NOT NULL
                    GROUP BY chosen_id""",
                memory_ids,
            ).fetchall()
        except Exception:
            return {}
    out: dict[str, float] = {}
    for r in rows:
        # log-ish: 1 click ~ +FEEDBACK_WEIGHT, 10 clicks ~ +2*FEEDBACK_WEIGHT
        n = int(r["n"] or 0)
        if n <= 0:
            continue
        boost = 1.0 + FEEDBACK_WEIGHT * min(3.0, 1.0 + (n - 1) * 0.25)
        out[r["chosen_id"]] = boost
    return out
