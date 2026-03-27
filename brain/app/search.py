from typing import Optional
from .storage import keyword_search, get_memory, DB_PATH
from .chroma import chroma_search
from .summarise import embed


def reciprocal_rank_fusion(
    keyword_results: list[dict],
    semantic_results: list[dict],
    k: int = 60,
) -> list[str]:
    scores: dict[str, float] = {}
    for rank, item in enumerate(keyword_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
    for rank, item in enumerate(semantic_results):
        id_ = item["id"]
        scores[id_] = scores.get(id_, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.keys(), key=lambda x: scores[x], reverse=True)


async def hybrid_search(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
) -> list[dict]:
    # Keyword search via FTS5
    kw_results = keyword_search(
        query, limit=20, project=project, type_filter=type_filter, days=days, db_path=DB_PATH
    )

    # Semantic search via ChromaDB
    embedding = await embed(query)
    where = {}
    if project:
        where["project"] = project
    if type_filter:
        where["type"] = type_filter
    sem_results = chroma_search(embedding, n_results=20, where=where or None)

    # Merge via Reciprocal Rank Fusion
    merged_ids = reciprocal_rank_fusion(kw_results, sem_results)[:limit]

    # Build output — summaries only, never full content
    kw_by_id = {r["id"]: r for r in kw_results}
    output = []
    for id_ in merged_ids:
        if id_ in kw_by_id:
            output.append(kw_by_id[id_])
        else:
            # semantic-only hit — fetch summary from DB
            entry = get_memory(id_, db_path=DB_PATH)
            if entry:
                output.append({
                    "id": entry.id, "summary": entry.summary,
                    "type": entry.type, "project": entry.project,
                    "source": entry.source, "importance": entry.importance,
                    "timestamp": entry.timestamp.isoformat(),
                })
    return output
