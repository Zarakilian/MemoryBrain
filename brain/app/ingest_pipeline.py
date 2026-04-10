import asyncio
import logging
from .models import MemoryEntry, Project, validate_entry
from .storage import add_memory, delete_memory, upsert_project, DB_PATH
from .chroma import chroma_add
from .summarise import embed, summarise, score_importance

logger = logging.getLogger(__name__)

MAX_CONCURRENT_INGESTS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INGESTS)


async def ingest(entry: MemoryEntry) -> MemoryEntry:
    """Full ingest pipeline: validate → summarise → score → embed → store SQLite + ChromaDB."""
    async with _semaphore:
        return await _ingest_inner(entry)


async def _ingest_inner(entry: MemoryEntry) -> MemoryEntry:
    validate_entry(entry)

    if not entry.summary:
        entry.summary = await summarise(entry.content)
    if entry.importance == 3:  # only score if caller left the default
        entry.importance = await score_importance(entry.content)

    embedding = await embed(entry.content)
    add_memory(entry, db_path=DB_PATH)
    try:
        chroma_add(
            memory_id=entry.id,
            embedding=embedding,
            metadata={"project": entry.project, "type": entry.type},
        )
    except Exception:
        # Roll back SQLite insert to prevent orphan
        logger.error(f"ChromaDB write failed for {entry.id} — rolling back SQLite entry")
        delete_memory(entry.id, db_path=DB_PATH)
        raise
    upsert_project(
        Project(slug=entry.project, name=entry.project.replace("-", " ").title()),
        db_path=DB_PATH,
    )
    return entry
