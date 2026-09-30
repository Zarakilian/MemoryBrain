from pathlib import Path

from fastapi import APIRouter, HTTPException, Response, UploadFile, File
from pydantic import BaseModel
from ..models import MemoryEntry, ValidationError
from ..ingest_pipeline import ingest, write_report

router = APIRouter()

MAX_UPLOAD_BYTES = 1_048_576  # 1 MB


class NoteRequest(BaseModel):
    content: str
    project: str
    tags: list[str] = []
    source: str = ""


async def _ingest_or_422(entry: MemoryEntry) -> MemoryEntry:
    try:
        return await ingest(entry)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/ingest/note", status_code=201)
async def ingest_note(req: NoteRequest, response: Response):
    """Store a note. 201 with the write report; 200 with duplicate=true when the
    same content is already stored for the project; 422 on invalid input."""
    entry = MemoryEntry(
        content=req.content,
        type="note",
        project=req.project,
        tags=req.tags,
        source=req.source,
        writer="rest",
    )
    result = await _ingest_or_422(entry)
    if result.duplicate:
        response.status_code = 200
    return write_report(result)


@router.post("/ingest/file", status_code=201)
async def ingest_file(project: str, response: Response, file: UploadFile = File(...)):
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
        writer="rest",
    )
    result = await _ingest_or_422(entry)
    if result.duplicate:
        response.status_code = 200
    return {**write_report(result), "filename": safe_filename}
