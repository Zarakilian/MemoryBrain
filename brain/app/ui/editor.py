# brain/app/ui/editor.py
"""UI editing endpoints — the one deliberate exception to the read-only UI.

Everything under /api/ui/edit/* is EXCLUDED from the UI auth bypass in
main.py: when BRAIN_API_KEY is set, these endpoints demand the X-Brain-Key
header exactly like /ingest/*. Reads elsewhere in the UI stay on
PRAGMA query_only connections; every write here goes through the same
storage/ingest layer the MCP tools use.

Guardrails:
- "remove" defaults to archiving (reversible); hard delete requires the
  caller to echo the first 8 characters of the memory id
- a project cannot be deleted while any memory (active or archived)
  still references it
- adding notes reuses the full ingest pipeline (summary, embedding,
  graph links); if the AI provider is down the note is still stored,
  flagged degraded, with summary/importance defaults
"""
from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..ingest_pipeline import ingest
from ..models import MemoryEntry, Project
from ..storage import (DB_PATH, add_memory, delete_memory, get_memory,
                       get_memory_by_content_hash, get_project, record_recall,
                       upsert_project)
from ..vector import vec_delete
from . import queries as q

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ui-edit"])

EDITABLE_TYPES = ("note", "fact", "reference")
ALL_TYPES = q.VALID_TYPES


def _rw() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------- projects

class ProjectBody(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    one_liner: str = Field(default="", max_length=300)


@router.post("/api/ui/edit/projects", status_code=201)
def create_or_update_project(body: ProjectBody):
    existed = get_project(body.slug, db_path=DB_PATH) is not None
    upsert_project(Project(slug=body.slug, name=body.name,
                           one_liner=body.one_liner), db_path=DB_PATH)
    return {"slug": body.slug, "name": body.name,
            "one_liner": body.one_liner, "created": not existed}


@router.delete("/api/ui/edit/projects/{slug}")
def delete_project(slug: str):
    if get_project(slug, db_path=DB_PATH) is None:
        raise HTTPException(404, "Unknown project")
    with _rw() as conn:
        n = conn.execute("SELECT COUNT(*) FROM memories WHERE project = ?",
                         (slug,)).fetchone()[0]
        if n:
            raise HTTPException(409, f"Project still holds {n} memories "
                                     "(archived ones included). Move or delete "
                                     "them first — this guardrail is deliberate.")
        conn.execute("DELETE FROM projects WHERE slug = ?", (slug,))
        conn.commit()
    return {"deleted": True, "slug": slug}


# ------------------------------------------------------------- memories

@router.post("/api/ui/edit/memories/{memory_id}/recall")
def recall_memory(memory_id: str):
    """Reinforcement signal from the UI: opening a memory in the inspector
    counts as a recall (fire-and-forget from the client; a lost signal is
    harmless). The only 'write' is strength/last_recalled."""
    if record_recall([memory_id], db_path=DB_PATH) == 0:
        raise HTTPException(404, "Memory not found")
    return {"recalled": memory_id}


class NoteBody(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    project: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    type: str = "note"
    tags: list[str] = []
    source: str = ""
    importance: int | None = Field(default=None, ge=1, le=5)


@router.post("/api/ui/edit/notes", status_code=201)
async def add_note(body: NoteBody):
    if body.type not in EDITABLE_TYPES:
        raise HTTPException(422, f"type must be one of {EDITABLE_TYPES}")
    existing = get_memory_by_content_hash(body.content, body.project, db_path=DB_PATH)
    if existing:
        return {"id": existing.id, "summary": existing.summary, "duplicate": True}
    entry = MemoryEntry(content=body.content, type=body.type, project=body.project,
                        tags=body.tags, source=body.source)
    if body.importance:
        entry.importance = body.importance
    try:
        result = await ingest(entry)
        return {"id": result.id, "summary": result.summary,
                "importance": result.importance, "degraded": False}
    except Exception:
        logger.warning("Ingest pipeline unavailable — storing note without "
                       "embedding/links (degraded)")
        entry.summary = entry.summary or (body.content[:180].strip()
                                          + ("…" if len(body.content) > 180 else ""))
        add_memory(entry, db_path=DB_PATH)
        return {"id": entry.id, "summary": entry.summary,
                "importance": entry.importance, "degraded": True}


class MemoryPatch(BaseModel):
    summary: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, min_length=1, max_length=200_000)
    type: str | None = None
    project: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    tags: list[str] | None = None
    importance: int | None = Field(default=None, ge=1, le=5)
    status: str | None = None


@router.patch("/api/ui/edit/memories/{memory_id}")
async def patch_memory(memory_id: str, body: MemoryPatch):
    entry = get_memory(memory_id, db_path=DB_PATH)
    if entry is None:
        raise HTTPException(404, "Memory not found")
    if body.type is not None and body.type not in ALL_TYPES:
        raise HTTPException(422, f"type must be one of {ALL_TYPES}")
    if body.status is not None and body.status not in ("active", "archived"):
        raise HTTPException(422, "status must be active or archived")
    if body.project is not None and get_project(body.project, db_path=DB_PATH) is None:
        raise HTTPException(422, f"Unknown project: {body.project}")

    fields, params = [], []
    for col in ("summary", "content", "type", "project", "importance", "status"):
        val = getattr(body, col)
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val)
    if body.tags is not None:
        fields.append("tags = ?")
        params.append(json.dumps(body.tags))
    if not fields:
        raise HTTPException(422, "Nothing to update")
    params.append(memory_id)
    with _rw() as conn:
        conn.execute(f"UPDATE memories SET {' , '.join(fields)} WHERE id = ?",
                     tuple(params))
        conn.commit()

    relinked = False
    if body.content is not None or body.tags is not None:
        # content changed: re-embed and re-derive this memory's edges.
        # Best effort — a downed provider must never block the edit itself.
        try:
            from ..summarise import embed
            from ..linker import link_new_memory
            from ..vector import vec_add
            updated = get_memory(memory_id, db_path=DB_PATH)
            emb = await embed(updated.content)
            vec_delete(memory_id, db_path=DB_PATH)
            vec_add(memory_id, emb,
                    {"project": updated.project, "type": updated.type},
                    db_path=DB_PATH)
            with _rw() as conn:
                conn.execute("DELETE FROM memory_links WHERE src_id = ? OR dst_id = ?",
                             (memory_id, memory_id))
                conn.commit()
            link_new_memory(updated, emb, db_path=DB_PATH)
            relinked = True
        except Exception:
            logger.warning("Re-embed after edit failed — text updated, "
                           "vector/links unchanged")
    return {"id": memory_id, "updated": sorted(f.split(" ")[0] for f in fields),
            "relinked": relinked}


@router.post("/api/ui/edit/memories/{memory_id}/archive")
def archive(memory_id: str):
    if get_memory(memory_id, db_path=DB_PATH) is None:
        raise HTTPException(404, "Memory not found")
    with _rw() as conn:
        conn.execute("UPDATE memories SET status = 'archived' WHERE id = ?",
                     (memory_id,))
        conn.commit()
    return {"id": memory_id, "status": "archived"}


class DeleteBody(BaseModel):
    confirm: str


@router.delete("/api/ui/edit/memories/{memory_id}")
def hard_delete(memory_id: str, body: DeleteBody):
    entry = get_memory(memory_id, db_path=DB_PATH)
    if entry is None:
        raise HTTPException(404, "Memory not found")
    if body.confirm != memory_id[:8]:
        raise HTTPException(400, "Confirmation mismatch: type the first 8 "
                                 "characters of the memory id to hard-delete. "
                                 "(Archiving is the reversible alternative.)")
    delete_memory(memory_id, db_path=DB_PATH)
    try:
        vec_delete(memory_id, db_path=DB_PATH)
    except Exception:
        pass
    with _rw() as conn:
        conn.execute("DELETE FROM memory_links WHERE src_id = ? OR dst_id = ?",
                     (memory_id, memory_id))
        conn.commit()
    return {"deleted": True, "id": memory_id}
