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

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import connect
from ..ingest_pipeline import ingest
from ..models import MemoryEntry, Project, ValidationError
from ..redact import redact, scrub
from ..storage import (DB_PATH, DERIVED_EDGE_KINDS, archive_memory_audited, content_hash,
                       audit, get_memory, get_project, hard_delete_memory, record_recall,
                       restore_memory, set_belief_status, upsert_project)
from ..vector import vec_delete
from . import queries as q

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ui-edit"])

EDITABLE_TYPES = ("note", "fact", "reference")
ALL_TYPES = q.VALID_TYPES


def _rw() -> sqlite3.Connection:
    return connect(DB_PATH)


# ------------------------------------------------------------- projects

class ProjectBody(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=120)
    one_liner: str = Field(default="", max_length=300)


@router.post("/api/ui/edit/projects", status_code=201)
def create_or_update_project(body: ProjectBody):
    existed = get_project(body.slug, db_path=DB_PATH) is not None
    body.name, body.one_liner = scrub(body.name), scrub(body.one_liner)
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


# ------------------------------------------------------- consolidation

class SleepBody(BaseModel):
    project: str = Field(default="", max_length=64)
    idle_days: int = Field(default=14, ge=1, le=365)


@router.post("/api/ui/edit/consolidate")
async def run_consolidation(body: SleepBody | None = None):
    """The Sleep button: run one consolidation cycle from the UI. Same
    behaviour as POST /admin/consolidate, but living under /api/ui/edit/*
    so the UI keeps exactly one write path and one auth story
    (X-Brain-Key when set)."""
    from ..consolidate import consolidate
    body = body or SleepBody()
    return await consolidate(project=body.project or None,
                             idle_days=body.idle_days)


# ----------------------------------------------------------- conflicts

class ConflictBody(BaseModel):
    a_id: str = Field(min_length=1, max_length=64)
    b_id: str = Field(min_length=1, max_length=64)


@router.post("/api/ui/edit/procedures/{memory_id}/confirm")
def confirm_rule(memory_id: str):
    """Only a person makes a learned rule official (trust user)."""
    from ..procedures import confirm_procedure
    if not confirm_procedure(memory_id, actor="ui", db_path=DB_PATH):
        raise HTTPException(404, "No proposed rule with that id")
    return {"id": memory_id, "status": "active"}


@router.post("/api/ui/edit/procedures/{memory_id}/reject")
def reject_rule(memory_id: str):
    from ..procedures import reject_procedure
    if not reject_procedure(memory_id, actor="ui", db_path=DB_PATH):
        raise HTTPException(404, "No proposed rule with that id")
    return {"id": memory_id, "status": "archived"}


@router.post("/api/ui/edit/beliefs/{memory_id}/approve")
def approve_belief(memory_id: str):
    """A proposed belief becomes active: it reaches the brief and search."""
    if not set_belief_status(memory_id, approve=True, actor="ui", db_path=DB_PATH):
        raise HTTPException(404, "No proposed belief with that id")
    return {"id": memory_id, "status": "active"}


@router.post("/api/ui/edit/beliefs/{memory_id}/reject")
def reject_belief(memory_id: str):
    """A proposed belief is archived (reversible), never shown as truth."""
    if not set_belief_status(memory_id, approve=False, actor="ui", db_path=DB_PATH):
        raise HTTPException(404, "No proposed belief with that id")
    return {"id": memory_id, "status": "archived"}


@router.post("/api/ui/edit/conflicts/dismiss")
def dismiss_conflict(body: ConflictBody):
    """'Keep both' — shared logic with MCP dismiss_conflict."""
    from ..conflicts import dismiss_conflict as _dismiss
    result = _dismiss(body.a_id, body.b_id, db_path=DB_PATH)
    if "error" in result:
        code = 422 if "differ" in result["error"] else 404
        raise HTTPException(code, result["error"])
    return result


class ResolveBody(BaseModel):
    winner_id: str = Field(min_length=1, max_length=64)
    loser_id: str = Field(min_length=1, max_length=64)


@router.post("/api/ui/edit/conflicts/resolve")
def resolve_conflict(body: ResolveBody):
    """'This one is right' — shared logic with MCP resolve_conflict."""
    from ..conflicts import resolve_conflict as _resolve
    result = _resolve(body.winner_id, body.loser_id, db_path=DB_PATH)
    if "error" in result:
        err = result["error"]
        code = 422 if "differ" in err else 404
        raise HTTPException(code, err)
    return result


# ------------------------------------------------------------- memories

# ------------------------------------------------------------- policy

class PolicyBody(BaseModel):
    project: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    include_system: bool | None = None
    max_brief_chars: int | None = Field(default=None, ge=800, le=12000)
    default_tags: list[str] | None = None
    notes: str | None = Field(default=None, max_length=2000)


@router.get("/api/ui/policy/{project}")
def get_project_policy(project: str):
    from ..policy import get_policy
    return get_policy(project, db_path=DB_PATH)


@router.put("/api/ui/edit/policy")
def put_project_policy(body: PolicyBody):
    from ..policy import set_policy
    return set_policy(
        body.project,
        include_system=body.include_system,
        max_brief_chars=body.max_brief_chars,
        default_tags=body.default_tags,
        notes=body.notes,
        db_path=DB_PATH,
    )


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
    # Atlas is the person's own door: what they write here is trust=user.
    entry = MemoryEntry(content=body.content, type=body.type, project=body.project,
                        tags=body.tags, source=body.source, writer="ui", trust="user")
    if body.importance:
        entry.importance = body.importance
    # v3 ingest never fails because the AI provider is down: it stores the
    # text and reports what degraded (no vector yet, fallback summary).
    try:
        result = await ingest(entry)
    except ValidationError as exc:
        raise HTTPException(422, str(exc))
    if result.duplicate:
        return {"id": result.id, "summary": result.summary, "duplicate": True}
    degraded = not result.embedded or "summary fallback" in result.warnings
    return {"id": result.id, "summary": result.summary,
            "importance": result.importance, "degraded": degraded}


class MemoryPatch(BaseModel):
    summary: str | None = Field(default=None, max_length=2000)
    content: str | None = Field(default=None, min_length=1, max_length=100_000)
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
    if body.status == "active" and entry.status == "proposed":
        raise HTTPException(422, "a proposed belief or rule is made active with its Approve or "
                                 "Confirm button, not restored")
    if body.project is not None and get_project(body.project, db_path=DB_PATH) is None:
        raise HTTPException(422, f"Unknown project: {body.project}")
    _check_edit_limits(entry, body)
    old_tags = list(entry.tags or [])

    warnings: list[str] = []
    if body.content is not None:
        body.content, fired = redact(body.content)
        warnings += [f"redacted: {rule}" for rule in dict.fromkeys(fired)]
    if body.summary is not None:
        body.summary, fired = redact(body.summary)
        warnings += [f"redacted: {rule}" for rule in dict.fromkeys(fired)]

    fields, params = [], []
    for col in ("summary", "content", "type", "project", "importance"):
        val = getattr(body, col)
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val)
    if body.tags is not None:
        fields.append("tags = ?")
        params.append(json.dumps(scrub(body.tags)))
    if not fields and body.status is None:
        raise HTTPException(422, "Nothing to update")
    updated_cols = sorted(f.split(" ")[0] for f in fields)
    if body.content is not None or body.project is not None:
        new_content = body.content if body.content is not None else entry.content
        fields.append("content_hash = ?")
        params.append(content_hash(new_content, body.project or entry.project))
    if body.content is not None:
        fields.append("content_updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        # the vector still describes the old text until the re-index below
        # finishes; if it never does, the re-embed job picks this up
        fields.append("embedded = 0")
    if fields:
        with _rw() as conn:
            conn.execute(f"UPDATE memories SET {' , '.join(fields)} WHERE id = ?",
                         (*params, memory_id))
            conn.commit()
        audit(memory_id, "edit", actor="ui",
              reason=", ".join(c for c in updated_cols if c != "content_hash"
                               and c != "content_updated_at"), db_path=DB_PATH)
    if body.status is not None and body.status != entry.status:
        # Status changes go through the audited path so the trail is complete.
        if body.status == "archived":
            archive_memory_audited(memory_id, actor="ui", db_path=DB_PATH)
        else:
            restore_memory(memory_id, actor="ui", db_path=DB_PATH)
            if get_memory(memory_id, db_path=DB_PATH).status == "proposed":
                warnings.append("never approved: back in the approval queue")
        updated_cols.append("status")

    relinked = False
    if body.content is not None or body.tags is not None:
        relinked = await _reindex_after_edit(memory_id, content_changed=body.content is not None,
                                             old_tags=old_tags)
    return {"id": memory_id, "updated": sorted(updated_cols), "relinked": relinked,
            "warnings": warnings}


def _check_edit_limits(entry, body: "MemoryPatch") -> None:
    """An edit meets the limits a write meets: the type's size cap, the tag
    caps, and no second active copy of another memory's text."""
    from ..models import MAX_TAG_LENGTH, MAX_TAGS
    from ..storage import get_memory_by_content_hash
    from ..write_policy import FACT_DECISION_MAX, OPEN_LOOP_MAX
    new_type = body.type or entry.type
    new_content = body.content if body.content is not None else entry.content
    if new_type in ("fact", "decision") and len(new_content) > FACT_DECISION_MAX:
        raise HTTPException(422, f"a {new_type} holds at most {FACT_DECISION_MAX} characters; "
                                 "keep long narrative as a session or note")
    if new_type == "open_loop" and len(new_content) > OPEN_LOOP_MAX:
        raise HTTPException(422, f"an open loop holds at most {OPEN_LOOP_MAX} characters")
    if body.tags is not None:
        if len(body.tags) > MAX_TAGS:
            raise HTTPException(422, f"too many tags (max {MAX_TAGS})")
        if any(len(t) > MAX_TAG_LENGTH for t in body.tags):
            raise HTTPException(422, f"a tag holds at most {MAX_TAG_LENGTH} characters")
    if body.content is not None or body.project is not None:
        twin = get_memory_by_content_hash(new_content, body.project or entry.project,
                                          db_path=DB_PATH, active_only=True)
        if twin is not None and twin.id != entry.id:
            raise HTTPException(409, f"memory {twin.id} already holds this text")


async def _reindex_after_edit(memory_id: str, content_changed: bool,
                              old_tags: list | None = None) -> bool:
    """Re-embed (when the text changed) and re-derive this memory's edges.
    Only derived edge kinds are replaced: belief citations and conflict
    verdicts, dismissed ones included, stay. Best effort: a downed provider
    never blocks the edit itself (the re-embed job retries the vector)."""
    from ..linker import link_new_memory
    from ..indexing import index_memory_vectors
    from ..vector import vec_get

    updated = get_memory(memory_id, db_path=DB_PATH)
    try:
        if content_changed:
            from ..entities import index_entities
            # extraction is CPU work on up to 100,000 characters: keep it off
            # the event loop so other clients are not frozen behind it
            await asyncio.to_thread(index_entities, memory_id,
                                    f"{updated.summary or ''}\n{updated.content}",
                                    db_path=DB_PATH)
            result = await index_memory_vectors(memory_id, updated.content, db_path=DB_PATH)
            if not result["embedded"]:
                # The old vectors describe the old text: drop them so search
                # stops using them. embedded=0 queues the re-embed job.
                with _rw() as conn:
                    conn.execute("DELETE FROM vec_chunks WHERE memory_id = ?", (memory_id,))
                    conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
                    conn.commit()
                return False
        with _rw() as conn:
            conn.execute(
                f"""DELETE FROM memory_links WHERE (src_id = ? OR dst_id = ?)
                    AND kind IN ({','.join('?' * len(DERIVED_EDGE_KINDS))})""",
                (memory_id, memory_id, *DERIVED_EDGE_KINDS))
            conn.commit()
        embedding = vec_get(memory_id, db_path=DB_PATH)
        if embedding is None:
            return False
        if old_tags:
            # relinking counts the new tags; take the old ones off first
            from ..linker import drop_tag_stats
            drop_tag_stats(old_tags, DB_PATH)
        await asyncio.to_thread(link_new_memory, updated, embedding, db_path=DB_PATH)
        return True
    except Exception:
        logger.warning("Relink after edit failed: text updated, edges unchanged",
                       exc_info=True)
        return False


@router.post("/api/ui/edit/memories/{memory_id}/archive")
def archive(memory_id: str):
    if not archive_memory_audited(memory_id, actor="ui", db_path=DB_PATH):
        raise HTTPException(404, "Memory not found")
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
    hard_delete_memory(memory_id, actor="ui", db_path=DB_PATH)
    try:
        vec_delete(memory_id, db_path=DB_PATH)  # the legacy Chroma store, if in use
    except Exception:
        pass
    return {"deleted": True, "id": memory_id}
