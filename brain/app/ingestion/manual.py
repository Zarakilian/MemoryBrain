from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, UploadFile, File
from pydantic import BaseModel
from ..models import MemoryEntry, ValidationError
from ..ingest_pipeline import ingest
from ..storage import get_memory_by_content_hash, DB_PATH

router = APIRouter()

MAX_UPLOAD_BYTES = 1_048_576  # 1 MB


class NoteRequest(BaseModel):
    content: str
    project: str
    tags: list[str] = []
    source: str = ""


@router.post("/ingest/note", status_code=201)
async def ingest_note(req: NoteRequest, response: Response):
    # Dedup check: same content + project → return existing
    existing = get_memory_by_content_hash(req.content, req.project, db_path=DB_PATH)
    if existing:
        response.status_code = 200
        return {"id": existing.id, "summary": existing.summary, "importance": existing.importance, "duplicate": True}

    entry = MemoryEntry(
        content=req.content,
        type="note",
        project=req.project,
        tags=req.tags,
        source=req.source,
    )
    result = await ingest(entry)
    return {"id": result.id, "summary": result.summary, "importance": result.importance}


@router.post("/ingest/file", status_code=201)
async def ingest_file(project: str, file: UploadFile = File(...)):
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_BYTES // 1024} KB limit")
    content = raw.decode("utf-8", errors="replace")
    safe_filename = Path(file.filename or "upload").name  # strip any directory components
    entry = MemoryEntry(
        content=content,
        type="file",
        project=project,
        source=safe_filename,
    )
    result = await ingest(entry)
    return {"id": result.id, "filename": safe_filename, "summary": result.summary}
