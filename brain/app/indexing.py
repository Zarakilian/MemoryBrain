"""Write a memory's vectors under the current embedding model.

The memory row is stored first (it is the canonical record); this module then
embeds it. A provider failure never fails the caller: the row is flagged
embedded = 0 and the background re-embed (reembed.py) retries it later.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from . import storage as _storage
from .db import connect
from .summarise import embed_document, embed_model_id
from .vector import get_backend, vec_add

logger = logging.getLogger(__name__)


def _set_embedded(memory_id: str, embedded: bool, db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.execute("UPDATE memories SET embedded = ? WHERE id = ?", (int(embedded), memory_id))
        conn.commit()
    finally:
        conn.close()


def _chroma_metadata(memory_id: str, db_path: Path) -> dict:
    """The legacy Chroma backend filters on metadata; sqlite-vec joins the row."""
    if get_backend() != "chroma":
        return {}
    entry = _storage.get_memory(memory_id, db_path=db_path)
    return {"project": entry.project, "type": entry.type, "status": entry.status} if entry else {}


async def index_memory_vectors(memory_id: str, content: str,
                               db_path: Optional[Path] = None) -> dict:
    """Embed `content` as a document and store the memory's vector.

    Returns {"embedded", "model", "chunks", "error"}. Never raises for a
    provider error; storage errors still propagate."""
    path = db_path or _storage.DB_PATH
    model = embed_model_id()
    try:
        vector = await embed_document(content)
    except Exception as exc:  # provider down, model missing, bad input
        logger.debug("embedding failed for %s", memory_id, exc_info=True)
        _set_embedded(memory_id, False, path)
        return {"embedded": False, "model": model, "chunks": 0,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    vec_add(memory_id, vector, _chroma_metadata(memory_id, path), db_path=path, model=model)
    _set_embedded(memory_id, True, path)
    return {"embedded": True, "model": model, "chunks": 0, "error": None}
