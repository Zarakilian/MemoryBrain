from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel
from typing import Optional
from ..models import MemoryEntry
from ..ingest_pipeline import ingest

router = APIRouter()


class NoteRequest(BaseModel):
    content: str
    project: str
    tags: list[str] = []
    source: str = ""


@router.post("/ingest/note", status_code=201)
async def ingest_note(req: NoteRequest):
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
    content = (await file.read()).decode("utf-8", errors="replace")
    entry = MemoryEntry(
        content=content,
        type="file",
        project=project,
        source=file.filename or "",
    )
    result = await ingest(entry)
    return {"id": result.id, "filename": file.filename, "summary": result.summary}
