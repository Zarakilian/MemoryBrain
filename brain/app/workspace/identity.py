"""Project identity: name, description (with source precedence), home folders."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .. import storage as _st
from ..models import PROJECT_SLUG_RE, Project
from ..summarise import summarise
from . import store as ws

logger = logging.getLogger(__name__)

MAX_DESCRIPTION = 400
DRAFT_COOLDOWN_DAYS = 7
MIN_CORPUS_CHARS = 80
SOURCE_RANK = {"user": 3, "tool": 2, "auto": 1, "": 0}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_identity(slug: str, db_path: Path) -> dict:
    p = _st.get_project(slug, db_path=db_path)
    homes = ws.list_folders(db_path, project=slug)
    with _st._connect(db_path) as conn:
        row = conn.execute("SELECT description_updated_at FROM projects WHERE slug = ?", (slug,)).fetchone()
    return {
        "slug": slug,
        "name": p.name if p else slug.replace("-", " ").title(),
        "description": p.description if p else "",
        "description_source": p.description_source if p else "",
        "description_updated_at": row["description_updated_at"] if row else None,
        "last_activity": p.last_activity.isoformat() if p else None,
        "home_folders": [{"root_id": f["root_id"], "rel_path": f["rel_path"], "role": f["role"],
                          "label": f["label"], "how": f["how"], "confidence": f["confidence"],
                          "confirmed": f["confirmed"]} for f in homes],
    }


def header_line(identity: dict) -> str:
    line = f"**{identity['name']}** ({identity['slug']})."
    if identity.get("description"):
        line += f" {identity['description'].rstrip('.')}."
    homes = identity.get("home_folders") or []
    if homes:
        primary = sorted(homes, key=lambda h: (h["role"] != "primary", h["rel_path"]))
        shown = ", ".join(h["rel_path"] for h in primary[:2])
        extra = len(primary) - 2
        line += f" Home: {shown}" + (f" (+{extra})." if extra > 0 else ".")
    return line


def _write_description(slug: str, text: str, source: str, db_path: Path) -> bool:
    current = _st.get_project(slug, db_path=db_path)
    cur_rank = SOURCE_RANK.get(current.description_source if current else "", 0)
    if current and current.description and SOURCE_RANK.get(source, 0) < cur_rank:
        return False
    text = " ".join((text or "").split())[:MAX_DESCRIPTION]
    with _st._connect(db_path) as conn:
        conn.execute("""UPDATE projects SET description = ?, description_source = ?,
                        description_updated_at = ? WHERE slug = ?""",
                     (text, source, _now().isoformat(), slug))
        conn.commit()
    return True


def set_identity(slug: str, db_path: Path, *, name: Optional[str] = None,
                 description: Optional[str] = None, source: str = "tool",
                 home_path: Optional[str] = None, label: str = "", role: str = "primary") -> dict:
    slug = slug.strip()
    if not PROJECT_SLUG_RE.match(slug):
        raise ValueError(f"invalid project slug: {slug!r}")
    if source not in SOURCE_RANK or source == "":
        source = "tool"
    existing = _st.get_project(slug, db_path=db_path)
    if existing is None:
        _st.upsert_project(Project(slug=slug, name=name or slug.replace("-", " ").title()), db_path=db_path)
    if name:
        with _st._connect(db_path) as conn:
            conn.execute("UPDATE projects SET name = ? WHERE slug = ?", (name.strip(), slug))
            conn.commit()
    skipped = False
    if description is not None and description.strip():
        skipped = not _write_description(slug, description, source, db_path)
    binding = None
    files_under = 0
    if home_path:
        resolved = ws.resolve_abs_path(home_path, db_path)
        if resolved is None:
            binding = {"written": False, "reason": "path_outside_known_roots", "row": None,
                       "conflict_with": "", "home_path": home_path}
        else:
            root_id, rel = resolved
            binding = ws.bind_folder(root_id, rel, slug, "tool", db_path, label=label,
                                     role=role if role in ("primary", "repo", "related", "area", "archive") else "primary")
            files_under = ws.count_files(slug, db_path, folder=rel)
    return {"identity": get_identity(slug, db_path), "binding": binding,
            "files_under": files_under, "skipped_description": skipped}


def _corpus(slug: str, db_path: Path) -> str:
    from ..pins import list_pins
    parts: list[str] = []
    try:
        for p in list_pins(slug, db_path=db_path):
            if p.get("summary"):
                parts.append(p["summary"])
    except Exception:
        pass
    with _st._connect(db_path) as conn:
        rows = conn.execute(
            """SELECT summary, substr(content, 1, 240) AS preview FROM memories
               WHERE project = ? AND status = 'active' AND type IN ('belief', 'fact', 'decision')
               ORDER BY CASE type WHEN 'belief' THEN 0 ELSE 1 END, importance DESC, timestamp DESC
               LIMIT 12""", (slug,)).fetchall()
    for r in rows:
        # Prefer the content preview: a memory's `summary` is often a short
        # label (e.g. "fact 0") that starves the corpus even when the
        # underlying content is substantial. Fall back to summary only when
        # there is no content preview at all.
        parts.append(r["preview"] or r["summary"] or "")
    text = "\n".join(x for x in parts if x)
    return text[:1500]


async def maybe_draft_description(slug: str, db_path: Path, force: bool = False) -> bool:
    ident = get_identity(slug, db_path)
    if ident["description_source"] == "user":
        return False
    if ident["description"] and not force:
        return False
    stamp = ident.get("description_updated_at")
    if stamp and not force:
        try:
            if _now() - datetime.fromisoformat(stamp) < timedelta(days=DRAFT_COOLDOWN_DAYS):
                return False
        except ValueError:
            pass
    corpus = _corpus(slug, db_path)
    if len(corpus) < MIN_CORPUS_CHARS:
        return False
    prompt = ("In two plain sentences, say what this project is and what it is for. "
              "No history, no dates, no lists.\n\n" + corpus)
    try:
        text = await summarise(prompt, max_sentences=2)
    except Exception:
        logger.warning("description draft failed for %s", slug, exc_info=True)
        return False
    text = (text or "").strip()
    if not text or text.startswith("In two plain sentences"):
        return False
    return _write_description(slug, text, "auto", db_path)


async def backfill_descriptions(db_path: Path) -> dict:
    drafted, skipped = [], []
    for p in _st.list_projects(db_path=db_path):
        (drafted if await maybe_draft_description(p.slug, db_path) else skipped).append(p.slug)
    return {"drafted": drafted, "skipped": skipped}
