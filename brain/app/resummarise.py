"""Replace summaries a small model wrote with the opening of the text.

Before 3.3 every long memory without a summary was summarised by the summary
model, and a small local model gets details wrong. This swaps those summaries
for the opening of the body (summarise.opening_summary), which cannot invent
anything. A summary the writer gave is never touched.

Rows stored before 3.3 do not say who wrote their summary, so it has to be
told from how it reads. Length does not tell: a writer's line can be long.
Style does. A writing agent's line is telegraphic: an id or a tag up front,
semicolons, backticks, arrows, a word in capitals. The small model writes
two or three full narrative sentences ("The export was moved by the team on
March 2, 2026, with ..."), often with a "Here is a summary" opening.
looks_model_written keeps to that second shape and leaves anything else
alone, so it misses some model summaries rather than replace a good one.

The old summary is kept in the audit row.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import storage as _storage
from .summarise import opening_summary

MIN_BODY_CHARS = 400
DEFAULT_MIN_SUMMARY_CHARS = 200
MAX_EXAMPLES = 10
MODEL_SENTENCE_WORDS = 12

_PREAMBLE = re.compile(r"(?i)^(?:here(?:'s| is| are)|okay|sure)\b")
# marks a writing agent uses in a one-line summary and the small model does not
_WRITER_MARKS = (";", "`", "→", "|", "->", "=>", "—", "–")
# opens with an id, a tag in capitals, a number or a symbol: "INC0012345 ...", "DEPLOYED ..."
_ID_START = re.compile(r"^(?:[A-Z]+\d|[A-Z][A-Z0-9_-]{3,}\b|#|\d|\[|[^\w\s\"'(])")
_SHOUT = re.compile(r"\b[A-Z]{5,}\b")              # a word in capitals anywhere
_EARLY_COLON = re.compile(r"^[^.]{0,40}:")         # "Topic: what is true about it"
# a short word in capitals for emphasis ("does NOT return"), which the model does not write
_EMPHASIS = re.compile(r"\b(?:NOT|NO|NEVER|ONLY|ALL|UP|DOWN|BOTH|MUST|DONE|OPEN|NEW|OLD|LIVE|"
                       r"BACK|WRONG|FIXED|DEAD|YES)\b")
_MONTH_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December) \d{1,2}, \d{4}\b")
# "28 Sep" is how a writer shortens a date; the model spells the month out
_SHORT_DATE = re.compile(r"\b\d{1,2} (?:Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\b")
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'])")


def looks_model_written(summary: str) -> bool:
    """True for a summary in the small model's narrative style. Anything that
    carries a writer's mark is False: when unsure, the summary is kept."""
    text = " ".join((summary or "").split())
    if not text:
        return False
    if _PREAMBLE.match(text):
        return True
    if (any(mark in text for mark in _WRITER_MARKS) or _ID_START.match(text)
            or _SHOUT.search(text) or _EARLY_COLON.match(text) or _EMPHASIS.search(text)
            or _SHORT_DATE.search(text) or text[0].islower()):          # "tb_export on the archive looks like ..."
        return False
    sentences = [s for s in _SENTENCES.split(text) if s.strip()]
    if len(sentences) < 2 or not text.endswith((".", "!", "?")):
        return False
    average = sum(len(s.split()) for s in sentences) / len(sentences)
    return average >= MODEL_SENTENCE_WORDS or bool(_MONTH_DATE.search(text))


def _candidates(project: Optional[str], min_summary_chars: int, db_path: Path) -> list[dict]:
    sql = """SELECT id, summary, summary_source, content FROM memories
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
            if not new or new == old or row["content"].startswith(old[:60]):
                continue
            # a row that says the model wrote it is taken at its word; an
            # unlabelled one (stored before 3.3) must read like the model
            if row["summary_source"] == "model" or looks_model_written(old):
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
