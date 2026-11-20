from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .. import storage as _storage
from ..models import MemoryEntry, ValidationError
from ..ingest_pipeline import ingest, write_report

router = APIRouter()
# the capture hooks: before a compaction, and at the end of a session
HOOK_SOURCES = ("pre-compact", "session-end")
MAX_QUIET_MINUTES = 24 * 60


class SessionIngestRequest(BaseModel):
    content: str
    project: str
    source: str = ""
    summary: str = ""                     # the caller's one line, stored as written
    quiet_minutes: Optional[int] = None   # skip when a session log is this recent


def _recent_session_log(project: str, minutes: int) -> Optional[dict]:
    """The newest session or handover stored for this project in the last
    `minutes`, as {id, minutes_ago}; None when there is none."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=minutes)).isoformat()
    with _storage._connect(_storage.DB_PATH) as conn:
        row = conn.execute(
            """SELECT id, timestamp FROM memories
               WHERE project = ? AND status = 'active' AND type IN ('session', 'handover')
                 AND timestamp >= ? ORDER BY timestamp DESC LIMIT 1""",
            (project, cutoff)).fetchone()
    if row is None:
        return None
    try:
        when = datetime.fromisoformat(row["timestamp"])
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        ago = max(0, int((now - when).total_seconds() // 60))
    except ValueError:
        ago = 0
    return {"id": row["id"], "minutes_ago": ago}


@router.post("/ingest/session", status_code=201)
async def ingest_session(req: SessionIngestRequest, response: Response):
    """Store a session. 201 with the write report; 200 with duplicate=true when
    the same content is already stored for the project; 422 on invalid input.

    quiet_minutes (the session-end hook sends it): when a session or handover
    was stored for this project that recently, nothing is stored and the
    reply is 200 {"skipped": ..., "recent_id": ...}. A session that already
    logged itself is not captured a second time."""
    from_hook = req.source.startswith(HOOK_SOURCES)
    if req.quiet_minutes and req.quiet_minutes > 0:
        recent = _recent_session_log(req.project, min(req.quiet_minutes, MAX_QUIET_MINUTES))
        if recent:
            response.status_code = 200
            return {"skipped": f"a session log was written {recent['minutes_ago']} minutes ago",
                    "recent_id": recent["id"]}
    entry = MemoryEntry(
        content=req.content,
        type="session",
        project=req.project,
        source=req.source,
        writer="hook" if from_hook else "rest",
        tags=["auto-capture"] if from_hook else [],
    )
    if req.summary.strip():
        entry.summary, entry.summary_source = req.summary.strip(), "writer"
    try:
        result = await ingest(entry)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if result.duplicate:
        response.status_code = 200
    return write_report(result)
