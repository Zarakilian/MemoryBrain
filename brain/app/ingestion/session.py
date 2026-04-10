from fastapi import APIRouter, Response
from pydantic import BaseModel
from ..models import MemoryEntry
from ..ingest_pipeline import ingest
from ..storage import get_memory_by_content_hash, DB_PATH

router = APIRouter()


class SessionIngestRequest(BaseModel):
    content: str
    project: str
    source: str = ""


@router.post("/ingest/session", status_code=201)
async def ingest_session(req: SessionIngestRequest, response: Response):
    # Dedup check: same content + project → return existing
    existing = get_memory_by_content_hash(req.content, req.project, db_path=DB_PATH)
    if existing:
        response.status_code = 200
        return {"id": existing.id, "summary": existing.summary, "importance": existing.importance, "duplicate": True}

    entry = MemoryEntry(
        content=req.content,
        type="session",
        project=req.project,
        source=req.source,
    )
    result = await ingest(entry)
    return {"id": result.id, "summary": result.summary, "importance": result.importance}
