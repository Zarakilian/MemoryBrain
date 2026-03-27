from .models import MemoryEntry, Project
from .storage import add_memory, upsert_project, DB_PATH
from .chroma import chroma_add
from .summarise import embed, summarise, score_importance


async def ingest(entry: MemoryEntry) -> MemoryEntry:
    """Full ingest pipeline: summarise → score → embed → store SQLite + ChromaDB."""
    if not entry.summary:
        entry.summary = await summarise(entry.content)
    entry.importance = await score_importance(entry.content)

    embedding = await embed(entry.content)
    add_memory(entry, db_path=DB_PATH)
    chroma_add(
        memory_id=entry.id,
        embedding=embedding,
        metadata={"project": entry.project, "type": entry.type},
    )
    upsert_project(
        Project(slug=entry.project, name=entry.project.replace("-", " ").title()),
        db_path=DB_PATH,
    )
    return entry
