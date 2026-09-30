"""Throttled background re-embed.

Memories whose vector is missing, or was made by another model (2.x vectors
have model ''), are re-embedded oldest first, a few per minute, so a migrated
brain upgrades itself without a long pause at startup. Search keeps using
the old vectors meanwhile (see search.hybrid_search).
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Optional

from . import storage as _storage
from .db import connect
from .indexing import index_memory_vectors
from .summarise import embed_model_id

logger = logging.getLogger(__name__)

START_DELAY_S = 30     # let the provider load its models before the first batch
TICK_S = 60            # one batch per tick, so the rate is per minute
RETRY_AFTER_S = 1800   # a memory that failed waits this long before a retry
_recent_failures: dict[str, float] = {}  # memory id -> time.monotonic() of the failure

_PENDING = """FROM memories m LEFT JOIN vec_memories v ON v.memory_id = m.id
              WHERE v.memory_id IS NULL OR v.model != ?"""


def pending_count(db_path: Optional[Path] = None) -> int:
    """Memories without a vector for the current embedding model."""
    conn = connect(db_path or _storage.DB_PATH)
    try:
        return conn.execute(f"SELECT COUNT(*) {_PENDING}", (embed_model_id(),)).fetchone()[0]
    finally:
        conn.close()


def _candidates(limit: int, model: str, db_path: Path) -> list[tuple[str, str]]:
    """Oldest pending memories, skipping ones that failed recently so a few
    bad rows can never starve the rest of the queue."""
    now = time.monotonic()
    for memory_id, failed_at in list(_recent_failures.items()):
        if now - failed_at >= RETRY_AFTER_S:
            del _recent_failures[memory_id]
    conn = connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT m.id, m.content {_PENDING} ORDER BY m.timestamp ASC, m.id LIMIT ?",
            (model, limit + len(_recent_failures))).fetchall()
    finally:
        conn.close()
    return [(r["id"], r["content"]) for r in rows if r["id"] not in _recent_failures][:limit]


async def reembed_batch(limit: int, db_path: Optional[Path] = None) -> dict:
    """Re-embed up to `limit` pending memories. One failure is counted and
    logged, never stops the batch. Returns {"done", "failed", "pending"}."""
    path = db_path or _storage.DB_PATH
    done = failed = 0
    first_error = None
    for memory_id, content in _candidates(limit, embed_model_id(), path):
        result = await index_memory_vectors(memory_id, content, db_path=path)
        if result["embedded"]:
            done += 1
            _recent_failures.pop(memory_id, None)
        else:
            failed += 1
            _recent_failures[memory_id] = time.monotonic()
            first_error = first_error or result["error"]
    report = {"done": done, "failed": failed, "pending": pending_count(db_path=path)}
    if failed:
        logger.warning("re-embed: %s (first error: %s)", report, first_error)
    elif done:
        logger.info("re-embed: %s", report)
    return report


async def _stopped_within(stop: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def reembed_loop(stop: asyncio.Event, rate_per_min: int,
                       db_path: Optional[Path] = None) -> None:
    """Run one batch of `rate_per_min` memories per tick until `stop` is set."""
    if await _stopped_within(stop, START_DELAY_S):
        return
    while not stop.is_set():
        try:
            if pending_count(db_path=db_path) > 0:
                await reembed_batch(rate_per_min, db_path=db_path)
        except Exception:
            logger.exception("re-embed tick failed")
        if await _stopped_within(stop, TICK_S):
            return
