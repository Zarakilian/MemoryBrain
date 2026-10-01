# brain/app/ingest_pipeline.py
"""The write path: validate, redact, dedup, summarise, embed, store, link.

v3 rules:
- the text is always stored; a failing summariser or embedding model only
  degrades the write (warnings say how), and the background re-embed
  catches up on missing vectors later
- the row, its vectors and any supersession closures commit together
- long bodies get chunk vectors, so the tail of a long session is searchable
- similarity alone never archives sessions, notes or references; only
  near-identical facts and decisions (and beliefs among beliefs) close an
  older memory
- every write returns the same report (write_report)
"""
import asyncio
import logging

from .db import connect
from .indexing import chunk_text
from .models import MemoryEntry, Project, validate_entry
from .storage import (DB_PATH, close_superseded, count_chunks, get_memory,
                      get_memory_by_content_hash, insert_memory, upsert_project)
from .summarise import embed_documents, embed_model_id, score_importance, summarise
from .vector import get_backend, insert_vectors, vec_add, vec_search_multi, vec_update_metadata
from .write_policy import apply_write_policy

logger = logging.getLogger(__name__)

MAX_CONCURRENT_INGESTS = 3
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_INGESTS)

SHORT_SUMMARY_MAX = 400       # bodies this short are their own summary
FALLBACK_SUMMARY_CHARS = 280  # summary when the summariser fails
AUTO_CLOSE = 0.97             # facts and decisions: close the older one at or above

# warn: report the pair from this cosine similarity. auto: archive at or above.
# None disables. Sessions and handovers chain instead (linker session_chain).
SUPERSESSION_THRESHOLDS: dict[str, dict] = {
    "session":   {"auto": None, "warn": None},
    "handover":  {"auto": None, "warn": None},
    "note":      {"auto": None, "warn": 0.75},
    "fact":      {"auto": AUTO_CLOSE, "warn": 0.78},
    "decision":  {"auto": AUTO_CLOSE, "warn": 0.78},
    "open_loop": {"auto": None, "warn": 0.75},
    "procedure": {"auto": None, "warn": 0.75},
    "file":      {"auto": None, "warn": 0.72},
    "reference": {"auto": None, "warn": 0.80},
    "belief":    {"auto": 0.95, "warn": 0.85},
}
_DEFAULT_THRESHOLDS = {"auto": None, "warn": 0.75}


async def _check_supersession(
    entry: MemoryEntry, embedding: list[float], model: str
) -> tuple[list[str], list[dict]]:
    """Compare with active memories of the same project, type and embedding
    model. Return (ids to close, near matches to report)."""
    thresholds = SUPERSESSION_THRESHOLDS.get(entry.type, _DEFAULT_THRESHOLDS)
    auto_threshold, warn_threshold = thresholds["auto"], thresholds["warn"]
    if auto_threshold is None and warn_threshold is None:
        return [], []

    # Same type only: a belief is distilled FROM raw memories and embeds close
    # to them, so it may only ever replace an earlier belief.
    candidates = vec_search_multi(
        {model: embedding}, n_results=5,
        filters={"project": entry.project, "type": entry.type, "status": "active"},
        db_path=DB_PATH,
    )
    superseded: list[str] = []
    potential: list[dict] = []
    for candidate in candidates:
        similarity = round(1.0 - candidate["distance"], 4)
        cid = candidate["id"]
        # What the user wrote is only ever retired by the user: an agent's
        # near-copy is reported instead.
        theirs = (candidate.get("metadata") or {}).get("trust")
        if theirs is None:
            mem = get_memory(cid, db_path=DB_PATH)
            theirs = mem.trust if mem else "agent"
        protected = theirs == "user" and entry.trust != "user"
        if auto_threshold is not None and similarity >= auto_threshold and not protected:
            superseded.append(cid)
        elif warn_threshold is not None and similarity >= warn_threshold:
            mem = get_memory(cid, db_path=DB_PATH)
            potential.append({"id": cid, "similarity": similarity,
                              "summary": mem.summary if mem else ""})
    return superseded, potential


def write_report(entry: MemoryEntry) -> dict:
    """What every write returns: MCP add_memory, /ingest/session, /ingest/note."""
    return {
        "id": entry.id,
        "summary": entry.summary,
        "importance": entry.importance,
        "type": entry.type,
        "tags": list(entry.tags or []),
        "trust": entry.trust,
        "writer": entry.writer,
        "embedded": bool(entry.embedded),
        "chunks": entry.chunks,
        "content_chars": len(entry.content or ""),
        "superseded": list(entry.superseded),
        "potential_supersessions": list(entry.potential_supersessions),
        "warnings": list(entry.warnings),
        "duplicate": bool(entry.duplicate),
    }


async def ingest(entry: MemoryEntry) -> MemoryEntry:
    """Validate, redact, summarise, embed and store one memory.

    Returns the stored entry, or the existing active copy (duplicate=True)
    when the same content is already stored for the project."""
    async with _semaphore:
        return await _ingest_inner(entry)


async def _ensure_summary(entry: MemoryEntry, warnings: list[str]) -> None:
    if entry.summary:
        return
    if len(entry.content) <= SHORT_SUMMARY_MAX:
        entry.summary = entry.content
        return
    try:
        entry.summary = (await summarise(entry.content)).strip()
    except Exception as exc:
        logger.warning("summariser failed for %s (%s); using the opening",
                       entry.id, type(exc).__name__)
        entry.summary = ""
    if not entry.summary:
        entry.summary = entry.content[:FALLBACK_SUMMARY_CHARS]
        warnings.append("summary fallback")


async def _ensure_importance(entry: MemoryEntry) -> None:
    if entry.importance is not None:
        return
    try:
        entry.importance = max(1, min(5, int(await score_importance(entry.content))))
    except Exception as exc:
        logger.warning("importance scoring failed for %s (%s); using 3",
                       entry.id, type(exc).__name__)
        entry.importance = 3


async def _embed(entry: MemoryEntry, chunks: list, warnings: list[str]):
    """(model id, parent vector, chunk vectors); (\"\", None, []) when the
    provider cannot start or the model fails. Never raises."""
    try:
        model = embed_model_id()
        vectors = await embed_documents([entry.content] + [c.text for c in chunks])
    except Exception as exc:
        logger.warning("embedding failed for %s (%s); stored without vectors",
                       entry.id, type(exc).__name__)
        warnings.append(f"embedding failed: {type(exc).__name__}")
        return "", None, []
    return model, vectors[0], vectors[1:]


def _store(entry: MemoryEntry, model: str, parent, chunks: list, chunk_vectors: list,
           superseded: list[str], warnings: list[str]) -> None:
    """One transaction: the row, its vectors and any supersession closures.
    If it fails nothing is written and the error propagates. A vector store
    that will not load does not stop the write: the row is stored without
    vectors, flagged embedded=0, and the re-embed job adds them later."""
    on_sqlite_vec = get_backend() == "sqlite_vec"
    closed_at = entry.valid_from or entry.timestamp.isoformat()
    try:
        conn = connect(DB_PATH, vec=True)
    except Exception as exc:
        logger.warning("vector store unavailable for %s (%s); storing without vectors",
                       entry.id, type(exc).__name__)
        warnings.append(f"vector store unavailable: {type(exc).__name__}")
        parent, entry.embedded = None, False
        conn = connect(DB_PATH)
    try:
        with conn:
            insert_memory(conn, entry)
            if parent is not None and on_sqlite_vec:
                insert_vectors(conn, entry.id, parent, model,
                               [(c.ix, c.start, c.end, v) for c, v in zip(chunks, chunk_vectors)])
            for old_id in superseded:
                close_superseded(conn, old_id, entry.id, closed_at,
                                 actor=entry.writer or "ingest")
    finally:
        conn.close()
    if parent is not None and not on_sqlite_vec:  # legacy Chroma rollback backend
        try:
            vec_add(entry.id, parent, {"project": entry.project, "type": entry.type,
                                       "status": "active"}, db_path=DB_PATH, model=model)
        except Exception as exc:
            logger.warning("Chroma write failed for %s (%s)", entry.id, type(exc).__name__)
            warnings.append(f"embedding failed: {type(exc).__name__}")
            entry.embedded = False
            _flag_unembedded(entry.id)
    for old_id in superseded:
        try:
            vec_update_metadata(old_id, {"status": "archived"})  # no-op on sqlite-vec
        except Exception:
            logger.warning("could not mark %s archived in the vector store", old_id)


def _flag_unembedded(memory_id: str) -> None:
    conn = connect(DB_PATH)
    try:
        with conn:
            conn.execute("UPDATE memories SET embedded = 0 WHERE id = ?", (memory_id,))
    finally:
        conn.close()


async def _link(entry: MemoryEntry, embedding, superseded: list[str]) -> None:
    """Graph edges and workspace file links are cache: a failure is logged and
    never fails the write (the graph catches up on /admin/rebuild-graph).
    Without a vector the tag, reference and session_chain edges still form."""
    try:
        from . import linker
        await asyncio.to_thread(linker.link_new_memory, entry, embedding,
                                superseded_ids=superseded, db_path=DB_PATH)
    except Exception:
        logger.warning("linking failed for %s; run /admin/rebuild-graph", entry.id,
                       exc_info=True)


async def _ingest_inner(entry: MemoryEntry) -> MemoryEntry:
    validate_entry(entry)
    warnings = apply_write_policy(entry)  # redaction, limits, tags, default importance
    if warnings:
        logger.info("write_policy %s: %s", entry.project, "; ".join(warnings))

    existing = get_memory_by_content_hash(entry.content, entry.project, db_path=DB_PATH,
                                          active_only=True)
    if existing:
        existing.duplicate = True
        existing.warnings = warnings
        existing.chunks = count_chunks(existing.id, db_path=DB_PATH)
        return existing

    await _ensure_summary(entry, warnings)
    await _ensure_importance(entry)

    chunks = chunk_text(entry.content)
    model, parent, chunk_vectors = await _embed(entry, chunks, warnings)
    entry.embedded = parent is not None
    # A proposal (a belief awaiting approval) never retires anything.
    superseded, potential = ([], []) if parent is None or entry.status != "active" else \
        await _check_supersession(entry, parent, model)
    if entry.type in ("fact", "decision") and not entry.valid_from:
        entry.valid_from = entry.timestamp.isoformat()
    if superseded:
        entry.supersedes = superseded[0]

    # The model calls above can take seconds; another call may have stored the
    # same text meanwhile. No await between this check and the write.
    existing = get_memory_by_content_hash(entry.content, entry.project, db_path=DB_PATH,
                                          active_only=True)
    if existing:
        existing.duplicate = True
        existing.warnings = warnings
        existing.chunks = count_chunks(existing.id, db_path=DB_PATH)
        return existing

    _store(entry, model, parent, chunks, chunk_vectors, superseded, warnings)
    entry.superseded = superseded
    entry.potential_supersessions = potential
    entry.chunks = len(chunk_vectors)
    entry.warnings = warnings

    try:
        upsert_project(
            Project(slug=entry.project, name=entry.project.replace("-", " ").title()),
            db_path=DB_PATH,
        )
    except Exception:
        logger.warning("project upsert failed for %s", entry.project, exc_info=True)
    await _link(entry, parent, superseded)
    try:  # names the memory mentions (hosts, tickets, paths ...), off the event loop
        from .entities import index_entities
        await asyncio.to_thread(index_entities, entry.id, entry.content, DB_PATH)
    except Exception:
        logger.warning("entity indexing failed for %s", entry.id, exc_info=True)
    return entry
