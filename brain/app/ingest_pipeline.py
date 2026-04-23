# brain/app/ingest_pipeline.py
import asyncio
import logging
from typing import Optional
from .models import MemoryEntry, Project, validate_entry
from .storage import add_memory, delete_memory, upsert_project, archive_memory, set_supersedes, get_memory, DB_PATH
from .chroma import chroma_add, chroma_search, chroma_update_metadata, build_where
from .summarise import embed, summarise, score_importance

logger = logging.getLogger(__name__)

MAX_CONCURRENT_INGESTS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INGESTS)

SUPERSESSION_THRESHOLDS: dict[str, dict] = {
    "session":   {"auto": 0.80, "warn": 0.70},
    "handover":  {"auto": 0.80, "warn": 0.70},
    "note":      {"auto": 0.90, "warn": 0.75},
    "fact":      {"auto": 0.92, "warn": 0.78},
    "file":      {"auto": 0.85, "warn": 0.72},
    "reference": {"auto": None, "warn": 0.80},
}
_DEFAULT_THRESHOLDS = {"auto": 0.90, "warn": 0.75}


async def _check_supersession(
    entry: MemoryEntry, embedding: list[float]
) -> tuple[list[str], list[dict]]:
    """Scan for similar active memories. Return (superseded_ids, potential_list)."""
    thresholds = SUPERSESSION_THRESHOLDS.get(entry.type, _DEFAULT_THRESHOLDS)
    warn_threshold = thresholds["warn"]
    auto_threshold = thresholds["auto"]  # None for reference

    candidates = chroma_search(
        embedding, n_results=5,
        where=build_where({"project": entry.project, "status": "active"}),
    )

    superseded: list[str] = []
    potential: list[dict] = []

    for candidate in candidates:
        similarity = round(1.0 - candidate["distance"], 4)
        cid = candidate["id"]

        if auto_threshold is not None and similarity >= auto_threshold:
            superseded.append(cid)
        elif warn_threshold is not None and similarity >= warn_threshold:
            mem = get_memory(cid, db_path=DB_PATH)
            potential.append({
                "id": cid,
                "similarity": similarity,
                "summary": mem.summary if mem else "",
            })

    return superseded, potential


async def ingest(entry: MemoryEntry) -> MemoryEntry:
    """Full ingest pipeline: validate → summarise → score → embed → supersede → store."""
    async with _semaphore:
        return await _ingest_inner(entry)


async def _ingest_inner(entry: MemoryEntry) -> MemoryEntry:
    validate_entry(entry)

    if not entry.summary:
        entry.summary = await summarise(entry.content)
    if entry.importance == 3:
        entry.importance = await score_importance(entry.content)

    embedding = await embed(entry.content)

    # Supersession scan — before writing so we don't compare against ourselves
    superseded_ids, potential = await _check_supersession(entry, embedding)
    entry.superseded = superseded_ids
    entry.potential_supersessions = potential

    # Persist new memory
    add_memory(entry, db_path=DB_PATH)
    try:
        chroma_add(
            memory_id=entry.id,
            embedding=embedding,
            metadata={"project": entry.project, "type": entry.type, "status": "active"},
        )
    except Exception:
        logger.error(f"ChromaDB write failed for {entry.id} — rolling back SQLite entry")
        delete_memory(entry.id, db_path=DB_PATH)
        raise

    # Archive superseded memories (after new one is safely written)
    for old_id in superseded_ids:
        archive_memory(old_id, superseded_by=entry.id, db_path=DB_PATH)
        try:
            chroma_update_metadata(old_id, {"status": "archived"})
        except Exception:
            logger.warning(f"Could not update ChromaDB status for archived {old_id}")

    # Set back-reference on the new memory if it superseded something
    if superseded_ids:
        set_supersedes(entry.id, superseded_ids[0], db_path=DB_PATH)
        entry.supersedes = superseded_ids[0]

    upsert_project(
        Project(slug=entry.project, name=entry.project.replace("-", " ").title()),
        db_path=DB_PATH,
    )
    return entry
