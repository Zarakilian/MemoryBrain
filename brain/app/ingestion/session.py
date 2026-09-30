from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel
from ..models import MemoryEntry, ValidationError
from ..ingest_pipeline import ingest, write_report

router = APIRouter()


class SessionIngestRequest(BaseModel):
    content: str
    project: str
    source: str = ""


@router.post("/ingest/session", status_code=201)
async def ingest_session(req: SessionIngestRequest, response: Response):
    """Store a session. 201 with the write report; 200 with duplicate=true when
    the same content is already stored for the project; 422 on invalid input."""
    entry = MemoryEntry(
        content=req.content,
        type="session",
        project=req.project,
        source=req.source,
        writer="hook" if req.source.startswith("pre-compact") else "rest",
    )
    try:
        result = await ingest(entry)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result.duplicate:
        response.status_code = 200
    return write_report(result)
