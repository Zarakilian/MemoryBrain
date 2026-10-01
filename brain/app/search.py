"""Hybrid search: keyword (FTS5 BM25) and semantic (parent and chunk vectors),
fused with reciprocal rank fusion and a few bounded adjustments.

v3 rules:
- questions work: stopwords are dropped and the remaining terms are ORed,
  with prefix matching for terms of 4+ characters
- the tail of a long memory is findable through its chunk vectors
- only sessions, handovers and notes age; a fact stays as strong as the day
  it was written
- all adjustments together move a score by at most -30% / +30%
- no page full of one project's sessions
- as_of asks what was true at a moment, archived rows included
"""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

from .storage import DB_PATH, DECAYING_TYPES, _connect, effective_strength, keyword_search
from .summarise import embed, embed_model_id, embed_query
from .vector import legacy_vector_count, vec_search_multi

logger = logging.getLogger(__name__)

DEGRADED_SEMANTIC = "semantic search unavailable"
# How much reinforcement/decay sways ranking. strength ∈ [0.2, 3.0];
# the multiplier maps that to roughly [0.68, 1.4] — a thumb on the
# scale, never a veto. 0 disables.
STRENGTH_WEIGHT = float(os.getenv("MEMORYBRAIN_STRENGTH_WEIGHT", "0.4"))

RRF_K = 60
CANDIDATES = 30
# as_of filters after the candidates are fetched, so look deeper into history
AS_OF_CANDIDATES = 300
AGING_TYPES = DECAYING_TYPES  # sessions, handovers, notes
AGE_HALF_LIFE_DAYS = 45
AGE_FLOOR = 0.5
ADJUST_MIN, ADJUST_MAX = 0.7, 1.3
MAX_SESSIONS_PER_PROJECT = 2
EXCERPT_CHARS = 400

STOPWORDS = frozenset("""
a about above after again against all almost also am an and any are around as at
be because been before being below between both but by can cannot could did do does
doing during each either else ever every few for from further get gets
got had has have having he her here hers herself him himself his how i if in into is
it its itself just like may me might more most much must my myself no nor not now of
on once only or other ought our ours ourselves own same shall she should
so some such than that the their theirs them themselves then there these they this
those through thus to too under until upon us very was we were what when where
whether which while who whom whose why will with would yet you your yours yourself
yourselves
aren couldn didn doesn don hadn hasn haven isn ll mustn re shan shouldn ve wasn
weren won wouldn
""".split())
# down, up, out, off, over and done stay: in ops notes they are the answer
# ("which server is down"). The second block is what contractions leave.

_PHRASE = re.compile(r'"([^"]+)"')
_WORD = re.compile(r"[\w][\w.\-/:\\]*")


def query_terms(query: str) -> tuple[list[str], list[str]]:
    """(quoted phrases, words) of a query, lowercased, stopwords dropped."""
    phrases = [p.strip().lower() for p in _PHRASE.findall(query or "") if p.strip()]
    rest = _PHRASE.sub(" ", query or "").lower()
    words = [w.strip(".-/:\\") for w in _WORD.findall(rest)]
    return phrases, list(dict.fromkeys(w for w in words
                                       if len(w) > 1 and w not in STOPWORDS))


def build_fts_query(query: str) -> str:
    """An FTS5 MATCH expression: phrases and words ORed, prefix match for
    alphanumeric words of 4+ characters. "" when nothing is left."""
    phrases, words = query_terms(query)
    parts = ['"' + p.replace('"', '""') + '"' for p in phrases]
    for w in words:
        quoted = '"' + w.replace('"', '""') + '"'
        parts.append(quoted + "*" if len(w) >= 4 and w.isalnum() else quoted)
    return " OR ".join(parts)


def strength_factor(strength: float, weight: float = STRENGTH_WEIGHT) -> float:
    """Map a memory's strength to a bounded ranking multiplier."""
    if weight <= 0:
        return 1.0
    return 1.0 + weight * (min(max(strength, 0.2), 3.0) - 1.0)


def _parse_time(value: str) -> Optional[datetime]:
    try:
        ts = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def age_factor(memory_type: str, timestamp_str: str) -> float:
    """v3 recency: only sessions, handovers and notes age, halving every 45
    days down to a floor of 0.5. Facts, decisions and the rest do not age."""
    if memory_type not in AGING_TYPES:
        return 1.0
    ts = _parse_time(timestamp_str)
    if ts is None:
        return 1.0
    days_old = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds() / 86400)
    return max(AGE_FLOOR, 0.5 ** (days_old / AGE_HALF_LIFE_DAYS))


def fuse(keyword_ids: list[str], semantic_ids: list[str], k: int = None) -> dict[str, float]:
    """Reciprocal rank fusion of two ranked id lists: 1 / (k + rank) summed over
    the lists an id appears in (rank counted from 1)."""
    k = RRF_K if k is None else k
    scores: dict[str, float] = {}
    for ranked in (keyword_ids, semantic_ids):
        for rank, memory_id in enumerate(ranked):
            scores[memory_id] = scores.get(memory_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


def _valid_at(row, as_of: datetime) -> bool:
    """Was this memory current at that moment? An archived memory with no
    validity end was removed (deleted as wrong), not superseded: never."""
    if row["status"] == "archived" and not row["valid_to"]:
        return False
    ts, start, end = (_parse_time(row["timestamp"]), _parse_time(row["valid_from"] or ""),
                      _parse_time(row["valid_to"] or ""))
    return ((ts is None or ts <= as_of) and (start is None or start <= as_of)
            and (end is None or end > as_of))


def _as_of_moment(as_of: str) -> Optional[datetime]:
    """An ISO datetime, or a bare date meaning the end of that day (UTC).
    Anything else is a ValueError: a typo must not quietly mean "now"."""
    moment = _parse_time(as_of)
    if moment is None:
        raise ValueError(f"as_of must be an ISO date or datetime (2026-09-30), got {as_of!r}")
    if moment is not None and len(as_of.strip()) == 10:
        moment = moment.replace(hour=23, minute=59, second=59, microsecond=999999)
    return moment


def _rows(ids: list[str], db_path) -> dict:
    if not ids:
        return {}
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""SELECT m.id, m.summary, m.content, m.type, m.project, m.source, m.importance,
                       m.timestamp, m.status, m.valid_from, m.valid_to, m.strength,
                       m.last_recalled,
                       EXISTS (SELECT 1 FROM project_pins p WHERE p.memory_id = m.id) AS pinned
                FROM memories m WHERE m.id IN ({','.join('?' * len(ids))})""", ids).fetchall()
    return {r["id"]: r for r in rows}


def _window(text: str, terms: list[str], size: int = EXCERPT_CHARS) -> str:
    """Up to `size` characters of text, starting a little before the first
    query term it contains, so the excerpt shows why the memory matched."""
    flat = " ".join(text.split())
    low = flat.lower()
    hits = [p for p in (low.find(t) for t in terms if t) if p >= 0]
    start = max(0, min(hits) - size // 4) if hits else 0
    return flat[start:start + size]


def _diversify(ranked: list[str], rows: dict, limit: int,
               type_filter: Optional[str] = None) -> list[str]:
    """At most MAX_SESSIONS_PER_PROJECT sessions or handovers per project,
    unless the caller asked for that type: then the cap would hide the answer."""
    capped = type_filter not in ("session", "handover")
    picked, per_project = [], {}
    for memory_id in ranked:
        row = rows[memory_id]
        if capped and row["type"] in ("session", "handover"):
            n = per_project.get(row["project"], 0)
            if n >= MAX_SESSIONS_PER_PROJECT:
                continue
            per_project[row["project"]] = n + 1
        picked.append(memory_id)
        if len(picked) == limit:
            break
    return picked


async def hybrid_search(
    query: str,
    limit: int = 10,
    project: Optional[str] = None,
    type_filter: Optional[str] = None,
    days: Optional[int] = None,
    tags: Optional[list] = None,
    include_history: bool = False,
    db_path=None,
    as_of: Optional[str] = None,
) -> list[dict]:
    results, _degraded = await search_with_status(
        query, limit=limit, project=project, type_filter=type_filter, days=days,
        tags=tags, include_history=include_history, db_path=db_path, as_of=as_of)
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
    as_of: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    """hybrid_search plus a degraded note: when the embedding model fails the
    keyword results still come back, with DEGRADED_SEMANTIC as the note."""
    path = db_path or DB_PATH
    moment = _as_of_moment(as_of) if as_of else None
    history = include_history or moment is not None

    pool = AS_OF_CANDIDATES if moment is not None else CANDIDATES
    match = build_fts_query(query)
    kw_results = keyword_search(
        query, limit=pool, project=project, type_filter=type_filter, days=days,
        tags=tags, include_history=history, db_path=path, match=match) if match else []

    vec_filters: dict = {}
    if not history:
        vec_filters["status"] = "active"
    if project:
        vec_filters["project"] = project
    if type_filter:
        vec_filters["type"] = type_filter

    degraded = None
    try:
        if legacy_vector_count(db_path=path) > 0:
            # 2.x vectors were made without a prompt; match them with a raw
            # query until the background re-embed has replaced them all.
            current, raw = await asyncio.gather(embed_query(query), embed(query))
            query_vectors = {embed_model_id(): current, "": raw}
        else:
            query_vectors = {embed_model_id(): await embed_query(query)}
        sem_results = vec_search_multi(query_vectors, n_results=pool, filters=vec_filters,
                                       db_path=path, include_chunks=True)
    except Exception as exc:
        logger.warning("%s (%s): keyword results only", DEGRADED_SEMANTIC, type(exc).__name__)
        sem_results, degraded = [], DEGRADED_SEMANTIC

    kw_rank = {r["id"]: i for i, r in enumerate(kw_results)}
    sem_rank = {r["id"]: i for i, r in enumerate(sem_results)}
    snippets = {r["id"]: r.get("snippet") or "" for r in kw_results}
    chunks = {r["id"]: r.get("chunk") for r in sem_results}
    ids = list(dict.fromkeys([*kw_rank, *sem_rank]))
    rows = _rows(ids, path)
    # A proposal (a belief or rule awaiting the user) is never a search result,
    # history and as_of included.
    ids = [i for i in ids if i in rows and rows[i]["status"] != "proposed"
           and (moment is None or _valid_at(rows[i], moment))]

    try:
        from .retrieval import feedback_boosts
        phrases, words = query_terms(query)
        feedback = feedback_boosts(ids, db_path=path, query_terms=set(phrases) | set(words))
    except Exception:
        feedback = {}

    fused_scores = fuse([r["id"] for r in kw_results], [r["id"] for r in sem_results])
    scores = {}
    for i in ids:
        fused = fused_scores[i]
        row = rows[i]
        strength = effective_strength(row["strength"], row["timestamp"], row["last_recalled"],
                                      bool(row["pinned"]), memory_type=row["type"])
        adjust = (age_factor(row["type"], row["timestamp"]) * strength_factor(strength)
                  * feedback.get(i, 1.0))
        scores[i] = fused * min(ADJUST_MAX, max(ADJUST_MIN, adjust))
    ranked = sorted(ids, key=lambda i: scores[i], reverse=True)

    phrases, words = query_terms(query)
    terms = phrases + words
    output = []
    for i in _diversify(ranked, rows, limit, type_filter):
        row = rows[i]
        chunk = chunks.get(i)
        if chunk:
            excerpt = _window(row["content"][chunk["start"]:chunk["end"]], terms)
        elif snippets.get(i):
            excerpt = snippets[i]
        else:
            excerpt = _window(row["content"], terms)
        output.append({
            "id": i, "summary": row["summary"], "content_preview": row["content"][:200],
            "type": row["type"], "project": row["project"], "source": row["source"],
            "importance": row["importance"], "timestamp": row["timestamp"],
            "status": row["status"],
            "excerpt": " ".join(excerpt.split())[:EXCERPT_CHARS],
            "matched": "both" if (i in kw_rank and i in sem_rank)
                       else ("keyword" if i in kw_rank else "semantic"),
            "score": round(scores[i], 6),
        })
    return output, degraded
