# brain/app/vector.py
"""Vector store abstraction — v2.0.0.

Routes vector operations to one of two backends via MEMORYBRAIN_VECTOR_BACKEND:

  sqlite_vec (default)  embeddings live in the vec_memories table inside
                        brain.db (one file, one transaction boundary, exact
                        brute-force cosine KNN via the sqlite-vec extension).
  chroma                legacy v0.5.x behaviour (embedded ChromaDB under
                        /app/data/chroma). Rollback path: flip the env var.

The public functions mirror the shapes chroma.py exposed so callers
(ingest_pipeline, search, mcp.tools) stay simple:

  vec_add(memory_id, embedding, metadata)
  vec_update_metadata(memory_id, metadata)      # no-op on sqlite_vec: search
                                                # joins memories for live status
  vec_search(embedding, n_results, filters)     # flat {project,type,status}
  vec_delete(memory_id)
  startup_backfill()                            # one-time Chroma -> SQLite copy

Vector loss is never data loss: SQLite content is canonical and embeddings
are re-derivable via the provider (POST /admin/backfill-vectors).
"""
import logging
import os
import sqlite3
import struct
from pathlib import Path
from typing import Optional

from .storage import DB_PATH

logger = logging.getLogger(__name__)

BACKEND_ENV = "MEMORYBRAIN_VECTOR_BACKEND"
DEFAULT_BACKEND = "sqlite_vec"


def get_backend() -> str:
    backend = os.getenv(BACKEND_ENV, DEFAULT_BACKEND).strip().lower()
    if backend not in ("sqlite_vec", "chroma"):
        logger.warning(f"Unknown {BACKEND_ENV}={backend!r}, using {DEFAULT_BACKEND}")
        return DEFAULT_BACKEND
    return backend


# ---------------------------------------------------------------- sqlite-vec

def _serialize(embedding: list[float]) -> bytes:
    return struct.pack(f"{len(embedding)}f", *embedding)


def _connect_vec(db_path: Path) -> sqlite3.Connection:
    import sqlite_vec  # deferred: only needed for this backend

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn


def _sv_add(memory_id: str, embedding: list[float], db_path: Path):
    with _connect_vec(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO vec_memories (memory_id, dim, embedding) VALUES (?, ?, ?)",
            (memory_id, len(embedding), _serialize(embedding)),
        )
        conn.commit()


def _sv_search(
    embedding: list[float],
    n_results: int,
    filters: Optional[dict],
    db_path: Path,
) -> list[dict]:
    filters = filters or {}
    where = ["v.dim = ?"]
    params: list = [len(embedding)]
    for key in ("project", "type"):
        if filters.get(key):
            where.append(f"m.{key} = ?")
            params.append(filters[key])
    if filters.get("status"):
        where.append("m.status = ?")
        params.append(filters["status"])

    sql = f"""
        SELECT v.memory_id AS id, m.project, m.type, m.status, m.timestamp,
               vec_distance_cosine(v.embedding, ?) AS distance
        FROM vec_memories v JOIN memories m ON m.id = v.memory_id
        WHERE {' AND '.join(where)}
        ORDER BY distance LIMIT ?
    """
    with _connect_vec(db_path) as conn:
        rows = conn.execute(sql, [_serialize(embedding), *params, n_results]).fetchall()
    return [
        {
            "id": r["id"],
            "metadata": {
                "project": r["project"], "type": r["type"],
                "status": r["status"], "timestamp": r["timestamp"],
            },
            "distance": r["distance"],
        }
        for r in rows
    ]


def _sv_delete(memory_id: str, db_path: Path):
    with _connect_vec(db_path) as conn:
        conn.execute("DELETE FROM vec_memories WHERE memory_id = ?", (memory_id,))
        conn.commit()


# ------------------------------------------------------------- public API

def vec_add(memory_id: str, embedding: list[float], metadata: dict,
            db_path: Path = None):
    if get_backend() == "chroma":
        from .chroma import chroma_add
        chroma_add(memory_id, embedding, metadata)
    else:
        _sv_add(memory_id, embedding, db_path or DB_PATH)


def vec_update_metadata(memory_id: str, metadata: dict, db_path: Path = None):
    """Status flips (active/archived). sqlite_vec joins memories at query time,
    so canonical status in the memories table is already authoritative."""
    if get_backend() == "chroma":
        from .chroma import chroma_update_metadata
        chroma_update_metadata(memory_id, metadata)
    # sqlite_vec: intentional no-op


def vec_search(embedding: list[float], n_results: int = 20,
               filters: Optional[dict] = None, db_path: Path = None) -> list[dict]:
    if get_backend() == "chroma":
        from .chroma import chroma_search, build_where
        return chroma_search(embedding, n_results=n_results,
                             where=build_where(filters or {}))
    return _sv_search(embedding, n_results, filters, db_path or DB_PATH)


def vec_delete(memory_id: str, db_path: Path = None):
    if get_backend() == "chroma":
        from .chroma import chroma_delete
        chroma_delete(memory_id)
    else:
        _sv_delete(memory_id, db_path or DB_PATH)


def vec_count(db_path: Path = None) -> int:
    if get_backend() == "chroma":
        from .chroma import _get_collection
        return _get_collection().count()
    with _connect_vec(db_path or DB_PATH) as conn:
        return conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]


def vec_ready(db_path: Path = None) -> bool:
    """Readiness probe for the active backend."""
    try:
        vec_count(db_path=db_path)
        return True
    except Exception:
        logger.exception("Vector store readiness check failed")
        return False


# --------------------------------------------------------------- migration

def startup_backfill(db_path: Path = None, chroma_path: Path = None) -> dict:
    """One-time, idempotent Chroma -> vec_memories copy, run at every startup.

    Cheap when complete (one COUNT). Copies embeddings for any memory missing
    a vector. Memories whose embedding is absent from Chroma are reported and
    left for POST /admin/backfill-vectors (provider re-embed). Never modifies
    the Chroma directory — it stays intact as the rollback path until the
    user deletes it manually.
    """
    if get_backend() != "sqlite_vec":
        return {"backend": "chroma", "skipped": True}

    db_path = db_path or DB_PATH
    with _connect_vec(db_path) as conn:
        missing = [r["id"] for r in conn.execute(
            """SELECT m.id FROM memories m
               LEFT JOIN vec_memories v ON v.memory_id = m.id
               WHERE v.memory_id IS NULL""").fetchall()]
    report = {"backend": "sqlite_vec", "missing_before": len(missing),
              "copied_from_chroma": 0, "still_missing": 0}
    if not missing:
        return report

    from .chroma import CHROMA_PATH
    cpath = chroma_path or CHROMA_PATH
    embeddings: dict[str, list[float]] = {}
    if Path(cpath).exists():
        try:
            from .chroma import get_client, COLLECTION_NAME
            col = get_client(Path(cpath)).get_or_create_collection(
                COLLECTION_NAME, metadata={"hnsw:space": "cosine"})
            for i in range(0, len(missing), 200):
                batch = missing[i:i + 200]
                got = col.get(ids=batch, include=["embeddings"])
                for id_, emb in zip(got["ids"], got["embeddings"]):
                    if emb is not None and len(emb) > 0:
                        embeddings[id_] = list(emb)
        except Exception:
            logger.exception("Chroma backfill read failed — memories without "
                             "vectors are excluded from semantic search until "
                             "POST /admin/backfill-vectors is run")

    with _connect_vec(db_path) as conn:
        for id_, emb in embeddings.items():
            conn.execute(
                "INSERT OR REPLACE INTO vec_memories (memory_id, dim, embedding) VALUES (?, ?, ?)",
                (id_, len(emb), _serialize(emb)))
        conn.commit()

    report["copied_from_chroma"] = len(embeddings)
    report["still_missing"] = len(missing) - len(embeddings)
    if report["still_missing"]:
        logger.warning(f"{report['still_missing']} memories lack embeddings — "
                       "run POST /admin/backfill-vectors to re-embed them")
    else:
        logger.info(f"Vector backfill complete: {report['copied_from_chroma']} "
                    "embeddings copied from Chroma")
    return report


async def reembed_missing(db_path: Path = None, limit: int = 500) -> dict:
    """Re-embed memories that have no vector, via the active AI provider."""
    from .summarise import embed

    db_path = db_path or DB_PATH
    with _connect_vec(db_path) as conn:
        rows = conn.execute(
            """SELECT m.id, m.content FROM memories m
               LEFT JOIN vec_memories v ON v.memory_id = m.id
               WHERE v.memory_id IS NULL LIMIT ?""", (limit,)).fetchall()
    done = 0
    for r in rows:
        embedding = await embed(r["content"])
        _sv_add(r["id"], embedding, db_path)
        done += 1
    return {"reembedded": done}
