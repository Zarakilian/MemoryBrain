import logging
import os
from datetime import datetime, timezone
from typing import Optional
from .storage import keyword_search, get_memory, get_strengths, DB_PATH
from .vector import legacy_vector_count, vec_search_multi
from .summarise import embed, embed_model_id, embed_query

logger = logging.getLogger(__name__)

DEGRADED_SEMANTIC = "semantic search unavailable"
RECENCY_DECAY_RATE = float(os.getenv("RECENCY_DECAY_RATE", "0.02"))
# How much reinforcement/decay sways ranking. strength ∈ [0.2, 3.0];
# the multiplier maps that to roughly [0.68, 1.4] — a thumb on the
# scale, never a veto. 0 disables.
STRENGTH_WEIGHT = float(os.getenv("MEMORYBRAIN_STRENGTH_WEIGHT", "0.4"))


def strength_factor(strength: float, weight: float = STRENGTH_WEIGHT) -> float:
    """Map a memory's strength to a bounded ranking multiplier."""
    if weight <= 0:
        return 1.0
    return 1.0 + weight * (min(max(strength, 0.2), 3.0) - 1.0)


def recency_factor(timestamp_str: str, decay_rate: float) -> float:
    """Returns a score in (0, 1] — 1.0 for today, decaying gently with age."""
    try:
        ts = datetime.fromisoformat(timestamp_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        days_old = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        return 1.0 / (1.0 + max(0.0, days_old) * decay_rate)
    except Exception:
        return 1.0


def reciprocal_rank_fusion(
    keyword_results: list[dict],
    semantic_results: list[dict],
    k: int = 60,
    decay_rate: float = RECENCY_DECAY_RATE,
    strengths: Optional[dict] = None,
    feedback: Optional[dict] = None,
) -> list[str]:
    scores: dict[str, float] = {}
    ts_map: dict[str, str] = {}

    for rank, item in enumerate(keyword_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
        if "timestamp" in item:
            ts_map[id_] = item["timestamp"]

    for rank, item in enumerate(semantic_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
        if "timestamp" in item:
            ts_map.setdefault(id_, item["timestamp"])

    if decay_rate > 0:
        for id_ in scores:
            if id_ in ts_map:
                scores[id_] *= recency_factor(ts_map[id_], decay_rate)

    if strengths:
        # reinforcement/decay: well-used memories float, untouched ones sink
        for id_ in scores:
            if id_ in strengths:
                scores[id_] *= strength_factor(strengths[id_])

    if feedback:
        # v2.3: memories agents actually chose after search float a little
        for id_ in scores:
            if id_ in feedback:
                scores[id_] *= feedback[id_]

    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


async def hybrid_search(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
    db_path=None,
) -> list[dict]:
    results, _degraded = await search_with_status(
        query, limit=limit, project=project, type_filter=type_filter, days=days,
        tags=tags, include_history=include_history, db_path=db_path)
    return results


async def search_with_status(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
    db_path=None,
) -> tuple[list[dict], Optional[str]]:
    """hybrid_search plus a degraded note: when the embedding model fails the
    keyword results still come back, with DEGRADED_SEMANTIC as the note."""
    path = db_path or DB_PATH
    kw_results = keyword_search(
        query, limit=20, project=project, type_filter=type_filter,
        days=days, tags=tags, include_history=include_history, db_path=path,
    )

    vec_filters: dict = {}
    if not include_history:
        vec_filters["status"] = "active"
    if project:
        vec_filters["project"] = project
    if type_filter:
        vec_filters["type"] = type_filter

    degraded = None
    try:
        query_vectors = {embed_model_id(): await embed_query(query)}
        if legacy_vector_count(db_path=path) > 0:
            # 2.x vectors were made without a prompt; match them with a raw
            # query until the background re-embed has replaced them all.
            query_vectors[""] = await embed(query)
        sem_results = vec_search_multi(query_vectors, n_results=20, filters=vec_filters,
                                       db_path=path)
    except Exception as exc:
        logger.warning("%s (%s): keyword results only", DEGRADED_SEMANTIC, type(exc).__name__)
        sem_results, degraded = [], DEGRADED_SEMANTIC

    candidate_ids = list({r["id"] for r in kw_results}
                         | {r["id"] for r in sem_results})
    strengths = get_strengths(candidate_ids, db_path=path)
    try:
        from .retrieval import feedback_boosts
        feedback = feedback_boosts(candidate_ids, db_path=path)
    except Exception:
        feedback = {}

    merged_ids = reciprocal_rank_fusion(
        kw_results, sem_results, strengths=strengths, feedback=feedback,
    )[:limit]

    kw_by_id = {r["id"]: r for r in kw_results}
    output = []
    for id_ in merged_ids:
        if id_ in kw_by_id:
            output.append(kw_by_id[id_])
        else:
            entry = get_memory(id_, db_path=path)
            if entry:
                output.append({
                    "id": entry.id, "summary": entry.summary,
                    "type": entry.type, "project": entry.project,
                    "source": entry.source, "importance": entry.importance,
                    "timestamp": entry.timestamp.isoformat(),
                    "status": entry.status,
                })
    return output, degraded
