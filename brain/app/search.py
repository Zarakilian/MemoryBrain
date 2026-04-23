import os
from datetime import datetime, timezone
from typing import Optional
from .storage import keyword_search, get_memory, DB_PATH
from .chroma import chroma_search, build_where
from .summarise import embed

RECENCY_DECAY_RATE = float(os.getenv("RECENCY_DECAY_RATE", "0.02"))


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

    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


async def hybrid_search(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
) -> list[dict]:
    kw_results = keyword_search(
        query, limit=20, project=project, type_filter=type_filter,
        days=days, tags=tags, include_history=include_history, db_path=DB_PATH,
    )

    embedding = await embed(query)

    # Build ChromaDB where filters — use build_where() for correct 1.5.x syntax
    chroma_filters: dict = {}
    if not include_history:
        chroma_filters["status"] = "active"
    if project:
        chroma_filters["project"] = project
    if type_filter:
        chroma_filters["type"] = type_filter

    sem_results = chroma_search(
        embedding, n_results=20,
        where=build_where(chroma_filters),
    )

    merged_ids = reciprocal_rank_fusion(kw_results, sem_results)[:limit]

    kw_by_id = {r["id"]: r for r in kw_results}
    output = []
    for id_ in merged_ids:
        if id_ in kw_by_id:
            output.append(kw_by_id[id_])
        else:
            entry = get_memory(id_, db_path=DB_PATH)
            if entry:
                output.append({
                    "id": entry.id, "summary": entry.summary,
                    "type": entry.type, "project": entry.project,
                    "source": entry.source, "importance": entry.importance,
                    "timestamp": entry.timestamp.isoformat(),
                    "status": entry.status,
                })
    return output
