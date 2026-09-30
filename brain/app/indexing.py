"""Write a memory's vectors under the current embedding model.

A memory gets one parent vector for the whole body and, when the body is
long, chunk vectors for overlapping windows of it, so the tail of a long
session is searchable too. The ingest path and the background re-embed both
use chunk_text() here, so they produce identical vectors.

The memory row is stored first (it is the canonical record). A provider
failure never fails the caller: the row is flagged embedded = 0 and the
background re-embed (reembed.py) retries it later.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import storage as _storage
from .db import connect
from .summarise import embed_documents, embed_model_id
from .vector import get_backend, insert_vectors, vec_add

logger = logging.getLogger(__name__)

CHUNK_MIN_BODY = 1800   # bodies up to this length get no chunk vectors
CHUNK_SIZE = 900
CHUNK_OVERLAP = 150

_HEADING = re.compile(r"\n#{1,6} ")
_SENTENCE_END = re.compile(r"[.!?][)\"']?\s")


@dataclass(frozen=True)
class Chunk:
    ix: int
    start: int
    end: int
    text: str


def _cut(text: str, start: int, end: int) -> int:
    """Where to end a chunk that would otherwise stop at `end`: before a markdown
    heading, else after a blank line, else after a sentence, else at `end`.
    Cuts earlier than half a chunk are ignored so chunks stay useful."""
    floor = start + CHUNK_SIZE // 2
    window = text[floor:end]
    headings = [m.start() for m in _HEADING.finditer(window)]
    if headings:
        return floor + headings[-1] + 1
    blank = window.rfind("\n\n")
    if blank != -1:
        return floor + blank + 2
    sentences = [m.end() for m in _SENTENCE_END.finditer(window)]
    if sentences:
        return floor + sentences[-1]
    return end


def chunk_text(text: str) -> list[Chunk]:
    """Overlapping ~CHUNK_SIZE windows of a long body; [] for short ones."""
    if len(text) <= CHUNK_MIN_BODY:
        return []
    chunks: list[Chunk] = []
    start = 0
    while True:
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            end = _cut(text, start, end)
        chunks.append(Chunk(len(chunks), start, end, text[start:end]))
        if end >= len(text):
            return chunks
        start = max(end - CHUNK_OVERLAP, start + 1)


def _set_embedded(memory_id: str, embedded: bool, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE memories SET embedded = ? WHERE id = ?", (int(embedded), memory_id))
        conn.commit()
    finally:
        conn.close()


def _chroma_metadata(memory_id: str, db_path: Path) -> dict:
    """The legacy Chroma backend filters on metadata; sqlite-vec joins the row."""
    entry = _storage.get_memory(memory_id, db_path=db_path)
    return {"project": entry.project, "type": entry.type, "status": entry.status} if entry else {}


async def index_memory_vectors(memory_id: str, content: str,
                               db_path: Optional[Path] = None) -> dict:
    """Embed `content` (and its chunks) and store the memory's vectors.

    Returns {"embedded", "model", "chunks", "error"}. Never raises for a
    provider error; storage errors still propagate."""
    path = db_path or _storage.DB_PATH
    model = ""
    chunks = chunk_text(content)
    try:
        model = embed_model_id()
        vectors = await embed_documents([content] + [c.text for c in chunks])
    except Exception as exc:  # provider down, model missing, bad input
        logger.debug("embedding failed for %s", memory_id, exc_info=True)
        _set_embedded(memory_id, False, path)
        return {"embedded": False, "model": model, "chunks": 0,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    if get_backend() == "chroma":
        vec_add(memory_id, vectors[0], _chroma_metadata(memory_id, path), db_path=path)
        _set_embedded(memory_id, True, path)
        return {"embedded": True, "model": model, "chunks": 0, "error": None}
    conn = connect(path, vec=True)
    try:
        with conn:
            insert_vectors(conn, memory_id, vectors[0], model,
                           [(c.ix, c.start, c.end, v) for c, v in zip(chunks, vectors[1:])])
            conn.execute("UPDATE memories SET embedded = 1 WHERE id = ?", (memory_id,))
    finally:
        conn.close()
    return {"embedded": True, "model": model, "chunks": len(chunks), "error": None}
