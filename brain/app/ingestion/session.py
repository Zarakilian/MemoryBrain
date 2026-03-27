from fastapi import APIRouter
from pydantic import BaseModel
from ..models import MemoryEntry
from ..ingest_pipeline import ingest

router = APIRouter()


class SessionIngestRequest(BaseModel):
    content: str
    project: str
    source: str = ""


@router.post("/ingest/session", status_code=201)
async def ingest_session(req: SessionIngestRequest):
    entry = MemoryEntry(
        content=req.content,
        type="session",
        project=req.project,
        source=req.source,
    )
    result = await ingest(entry)
    return {"id": result.id, "summary": result.summary, "importance": result.importance}
