"""Replace summaries a small model wrote with the opening of the text.

Before 3.3 every long memory without a summary was summarised by the summary
model, and a small local model gets details wrong. This swaps those summaries
for the opening of the body (summarise.opening_summary), which cannot invent
anything. A summary the writer gave is never touched.

Rows stored before 3.3 do not say who wrote their summary. A summary counts as
model-written when the body is long, the summary is long too (a writer's line
is short) and it is not the opening of the body. The old summary is kept in
the audit row.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import storage as _storage
from .summarise import opening_summary

MIN_BODY_CHARS = 400
DEFAULT_MIN_SUMMARY_CHARS = 200
MAX_EXAMPLES = 10


def _candidates(project: Optional[str], min_summary_chars: int, db_path: Path) -> list[dict]:
    sql = """SELECT id, summary, content FROM memories
             WHERE status IN ('active', 'done') AND summary_source IN ('', 'model')
               AND length(content) > ? AND length(summary) >= ?"""
    params: list = [MIN_BODY_CHARS, min_summary_chars]
    if project:
        sql += " AND project = ?"
        params.append(project)
    out = []
    with _storage._connect(db_path) as conn:
        for row in conn.execute(sql + " ORDER BY timestamp", params).fetchall():
            new = opening_summary(row["content"])
            old = row["summary"] or ""
            if new and new != old and not row["content"].startswith(old[:60]):
                out.append({"id": row["id"], "old": old, "new": new})
    return out


def resummarise(project: Optional[str] = None, dry_run: bool = True,
                min_summary_chars: int = DEFAULT_MIN_SUMMARY_CHARS,
                actor: str = "system", db_path: Optional[Path] = None) -> dict:
    """List (dry_run, the default) or apply the replacements. Reversible by
    hand: every audit row holds the summary it replaced."""
    path = db_path or _storage.DB_PATH
    found = _candidates(project, max(1, int(min_summary_chars)), path)
    examples = found[:MAX_EXAMPLES]
    if dry_run:
        return {"dry_run": True, "would_change": len(found), "examples": examples}
    with _storage._connect(path) as conn:
        for item in found:
            conn.execute("UPDATE memories SET summary = ?, summary_source = 'extract' WHERE id = ?",
                         (item["new"], item["id"]))
            _storage._audit(conn, item["id"], "resummarise", actor,
                            "model summary replaced by the opening", {"old": item["old"]})
        conn.commit()
    return {"dry_run": False, "changed": len(found), "examples": examples}
