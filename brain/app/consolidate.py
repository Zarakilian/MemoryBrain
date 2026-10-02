# brain/app/consolidate.py
"""The consolidation cycle — the brain that sleeps.

What every passive notes vault is missing: between sessions, the brain
digests what it ate. One run does four things, in order:

  1. BELIEFS — clusters of tightly-linked active memories in a project are
     distilled (via the configured summarise provider) into a single
     `belief` memory that carries `derived_from` edges back to every
     source. Beliefs go through the normal ingest pipeline, so they are
     embedded, linked, and — crucially — a re-consolidation of the same
     cluster naturally supersedes the previous belief.
  2. SOURCE DAMPING — consolidated sources stay active and searchable but
     their strength is scaled down: the belief now speaks first for them.
  3. CONTRADICTIONS — pairs of active fact/belief memories in the same
     project whose embeddings sit in the supersession "warn zone" (very
     similar, yet neither superseded the other) are flagged with a
     `conflicts_with` edge. The brain never resolves these silently —
     they surface in the UI for a human verdict.
  4. OPEN LOOPS — unfinished business (TODO / FIXME / "next session" /
     open question lines) in recent sessions and handovers is extracted
     into `open_loop` memories so the next session starts from what is
     unfinished, not from silence. Deterministic (no LLM), deduplicated
     by content hash. A loop closes (status done) when a later session
     says, in one sentence, that most of it was done.

v3: beliefs cite their sources sentence by sentence and wait as
`proposed` until a human approves them; with MEMORYBRAIN_JUDGE=on the
model confirms each contradiction; decay is computed from time at read
time (storage.effective_strength), so a second run changes nothing; and
only one run happens at a time.

Everything here is additive and derived: no existing memory is deleted or
archived by consolidation itself (supersession of stale beliefs happens
through the same pipeline rules as everything else).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryEntry
from .storage import (DB_PATH, _audit, _connect, decay_strengths, get_memory_by_content_hash,
                      get_meta, set_meta)
from .summarise import complete, strip_preamble

logger = logging.getLogger(__name__)

# Clustering: edges at or above this combined weight bind a cluster.
CLUSTER_MIN_WEIGHT = 0.30
MIN_CLUSTER_SIZE = 3
MAX_CLUSTER_SOURCES = 12     # a belief distilled from dozens of memories is mush,
                             # and its hub-degree eats the constellation:
                             # oversized components get SPLIT (tighter edge
                             # thresholds first, then time-ordered chunks)
MAX_CLUSTERS_PER_PROJECT = 5
MAX_CORPUS_CHARS = 6000

# Contradiction "warn zone": similar enough to worry, not similar enough
# to have auto-superseded. Mirrors ingest_pipeline's thresholds.
CONFLICT_MIN_SIM = 0.78
# Fact-fact pairs are often sequential milestones (deploys, disk audits).
# Require a tighter band so nightly sleep does not re-flag every new
# "Deployed to prod: backend abc1234" fact against every earlier one.
CONFLICT_MIN_SIM_FACT = 0.88
CONFLICT_MAX_SIM = 0.92
CONFLICT_TYPES = ("fact", "belief")

# Both sides look like historical timeline / status logs → not a contradiction.
# Dismiss only tombstones the exact edge; without this, each new deploy tip
# spawns a combinatorial fan-out of conflicts_with edges every night.
# A commit hash: 7 to 40 hex characters with at least one digit.
_COMMIT_HASH = r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"
_TIMELINE_FACT_RE = re.compile(
    r"(?i)("
    r"\b(?:deploy(?:ed|ment)?|release[ds]?|shipped|rolled out|went live|is live"
    r"|prod(?:uction)?|staging)\b.*" + _COMMIT_HASH + r"|"
    r"\brelease v?\d+(?:\.\d+)+\b|"
    r"\d+(?:\.\d+)?\s*GB free\b"
    r")"
)


def _is_timeline_fact_summary(summary: Optional[str]) -> bool:
    return bool(_TIMELINE_FACT_RE.search(summary or ""))


def _skip_as_timeline_pair(
    type_a: str, type_b: str, sum_a: Optional[str], sum_b: Optional[str],
) -> bool:
    """Sequential deploy/status facts that should coexist as history."""
    if type_a != "fact" or type_b != "fact":
        return False
    return _is_timeline_fact_summary(sum_a) and _is_timeline_fact_summary(sum_b)

_LOOP_RE = re.compile(
    r"^.*(?:\bTODO\b|\bFIXME\b|\bnext session\b|\bstill need(?:s)? to\b"
    r"|\bopen question\b|\bunresolved\b|\bfollow[- ]up\b).*$",
    re.IGNORECASE | re.MULTILINE)
# "no follow-up needed", "nothing unresolved", "not a TODO": not loops at all.
_NEGATED_LOOP_RE = re.compile(
    r"\b(?:no|nothing|not(?: a| an)?|none|without)\b[^.\n]{0,24}?"
    r"\b(?:follow[- ]ups?|todos?|fixmes?|unresolved|open questions?|next session)\b"
    r"|\b(?:follow[- ]up|todo)\b[^.\n]{0,16}\bnot (?:needed|required)\b",
    re.IGNORECASE)
LOOP_TAG = "open_loop"
LOOP_LOOKBACK_DAYS = 30
MAX_LOOPS_PER_RUN = 12
LOOP_CLOSE_SHARE = 0.6
LOOP_MIN_MATCH = 2  # a one-word loop is never closed by a stray "done"
# "not done yet", "isn't fixed", "still open": a done-word that says the opposite
_NEGATION_RE = re.compile(r"\b(?:not|never|no longer|yet)\b|n't\b|\bstill\b", re.IGNORECASE)
_DONE_WORDS = frozenset({"done", "fixed", "closed", "resolved", "completed", "merged", "shipped"})
_LOOP_MARKER_WORDS = frozenset({"open", "loop", "todo", "fixme", "next", "session", "still",
                                "need", "needs", "question", "unresolved", "follow", "up"})

# One consolidation at a time: an in-process lock, plus a brain_meta marker
# so another process (or a crashed run) is noticed. Stale after 2 hours.
RUN_LOCK = asyncio.Lock()
META_RUNNING = "consolidation_running_since"
RUN_STALE_S = 2 * 3600
_CITE_RE = re.compile(r"\[m:([^\]\s]{1,16})\]")
# A sentence ends at . ! or ? followed by whitespace or the line end (with any
# citations right after it), so export.py, 2.5 and wiki.example.com stay whole.
_CITED_SENTENCE_RE = re.compile(
    r".+?(?:[.!?]+(?:[ \t]*\[m:[^\]\s]{1,16}\])*(?=\s|$)|$)", re.MULTILINE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------- clustering

def _active_rows(conn, project: str) -> list[dict]:
    rows = conn.execute(
        """SELECT id, summary, content, type, timestamp FROM memories
           WHERE status = 'active' AND project = ? AND type != 'belief'
           ORDER BY timestamp ASC""",
        (project,),
    ).fetchall()
    return [dict(r) for r in rows]


def _components(member_ids: set[str], edges: list[tuple],
                min_weight: float) -> list[list[str]]:
    parent = {m: m for m in member_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, w in edges:
        if w >= min_weight and a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

    groups: dict[str, list[str]] = {}
    for m in member_ids:
        groups.setdefault(find(m), []).append(m)
    return list(groups.values())


def _split_oversized(group: list[str], edges: list[tuple],
                     rows_by_id: dict, min_weight: float) -> list[list[str]]:
    """A component bigger than MAX_CLUSTER_SOURCES is not one truth.
    Tighten the edge threshold until it falls apart; whatever refuses to
    split gets chunked in time order."""
    if len(group) <= MAX_CLUSTER_SOURCES:
        return [group]
    if min_weight < 0.85:
        out = []
        for sub in _components(set(group), edges, min_weight + 0.15):
            out.extend(_split_oversized(sub, edges, rows_by_id,
                                        min_weight + 0.15))
        return out
    ordered = sorted(group, key=lambda m: rows_by_id[m]["timestamp"])
    return [ordered[i:i + MAX_CLUSTER_SOURCES]
            for i in range(0, len(ordered), MAX_CLUSTER_SOURCES)]


def _clusters(conn, member_ids: set[str],
              rows_by_id: dict) -> list[list[str]]:
    """Connected components over the existing memory graph, restricted to
    the given ids and to organic edge kinds above the weight floor —
    then split down to human-sized truths."""
    edges = [(e["src_id"], e["dst_id"], e["weight"]) for e in conn.execute(
        """SELECT src_id, dst_id, weight FROM memory_links_all
           WHERE weight >= ? AND kind IN
             ('semantic','tag','reference','session_chain')""",
        (CLUSTER_MIN_WEIGHT,),
    ).fetchall()]
    out = []
    for g in _components(member_ids, edges, CLUSTER_MIN_WEIGHT):
        if len(g) < MIN_CLUSTER_SIZE:
            continue
        for sub in _split_oversized(g, edges, rows_by_id, CLUSTER_MIN_WEIGHT):
            if len(sub) >= MIN_CLUSTER_SIZE:
                out.append(sub)
    out.sort(key=len, reverse=True)
    return out[:MAX_CLUSTERS_PER_PROJECT]


def _retire_bloated_beliefs(conn, project: str, db_path: Path) -> int:
    """Beliefs from before the size cap (derived from more sources than
    MAX_CLUSTER_SOURCES) are mush AND hub-monsters in the constellation.
    Archive them; the next clusters re-distil their ground properly."""
    from .storage import archive_memory
    rows = conn.execute(
        """SELECT b.id, COUNT(*) AS n FROM memories b
           JOIN memory_links l ON l.src_id = b.id AND l.kind = 'derived_from'
           WHERE b.status = 'active' AND b.type = 'belief' AND b.project = ?
           GROUP BY b.id HAVING n > ?""",
        (project, MAX_CLUSTER_SOURCES),
    ).fetchall()
    for r in rows:
        archive_memory(r["id"], superseded_by=None, db_path=db_path,
                       actor="consolidation", reason="belief cites too many sources")
        try:
            from .vector import vec_update_metadata
            vec_update_metadata(r["id"], {"status": "archived"})
        except Exception:
            pass
    return len(rows)


def _already_believed(conn, cluster: list[str]) -> bool:
    """True if an active or proposed belief already derives from (most of)
    this cluster, or the user rejected one that did: re-synthesising it would
    just churn tokens, or refill the review queue with what was turned down."""
    rows = conn.execute(
        f"""SELECT l.src_id, COUNT(*) AS n FROM memory_links l
            JOIN memories b ON b.id = l.src_id
            WHERE l.kind = 'derived_from'
              AND (b.status IN ('active', 'proposed')
                   OR (b.status = 'archived' AND EXISTS (
                       SELECT 1 FROM memory_audit a
                       WHERE a.memory_id = b.id AND a.action = 'reject')))
              AND l.dst_id IN ({','.join('?' * len(cluster))})
            GROUP BY l.src_id""",
        cluster,
    ).fetchall()
    best = max((r["n"] for r in rows), default=0)
    return best >= len(cluster)          # identical coverage → nothing new


def _tag(memory_id: str) -> str:
    return memory_id[:8]


def belief_prompt(rows_by_id: dict, cluster: list[str]) -> str:
    """Sources tagged [m:<id8>]; the model must end every sentence with the tag
    of the source it came from, so each claim in a belief can be checked."""
    lines = []
    for mid in sorted(cluster, key=lambda m: rows_by_id[m]["timestamp"]):
        r = rows_by_id[mid]
        text = " ".join((r["summary"] or r["content"] or "").split())
        lines.append(f"[m:{_tag(mid)}] {text}")
    sources = "\n".join(lines)[:MAX_CORPUS_CHARS]
    return ("Distil these related notes into their single current truth: the present "
            "state of affairs, decisions that stand, how things work now.\n"
            "Rules: at most 6 short sentences. Use only facts found in the sources. "
            "End EVERY sentence with the tag of the source it comes from, for example "
            "[m:1a2b3c4d]. Reply with the sentences only.\n\nSources:\n" + sources)


def cited_sentences(text: str, tags: set[str]) -> list[str]:
    """The sentences of a model answer that cite at least one real source and
    no invented one. Everything uncited is dropped."""
    kept = []
    for raw in _CITED_SENTENCE_RE.findall(strip_preamble(text or "")):
        sentence = raw.strip()
        cited = _CITE_RE.findall(sentence)
        if cited and all(c in tags for c in cited):
            kept.append(sentence)
    return kept


def _judge_key(pair: dict) -> str:
    lo, hi = sorted((pair["src"], pair["dst"]))
    return f"judged_no:{lo}:{hi}"


async def _judge_conflicts(pairs: list[dict], summaries: dict,
                           db_path: Optional[Path] = None) -> list[dict]:
    """With MEMORYBRAIN_JUDGE=on, keep only pairs the model calls a real
    contradiction (similar wording alone is not a conflict). A pair the model
    cleared is remembered and not sent to it again every night."""
    if os.getenv("MEMORYBRAIN_JUDGE", "").strip().lower() not in ("on", "1", "true", "yes"):
        return pairs
    db_path = db_path or DB_PATH
    kept = []
    for pair in pairs:
        if get_meta(_judge_key(pair), db_path=db_path):
            continue
        prompt = ("Do these two statements contradict each other? Answer YES or NO only.\n\n"
                  f"A: {summaries.get(pair['src'], '')}\nB: {summaries.get(pair['dst'], '')}")
        try:
            answer = await complete(prompt)
        except Exception:
            logger.warning("Consolidation: the contradiction judge failed", exc_info=True)
            continue
        if answer.strip().upper().startswith("YES"):
            kept.append({**pair, "meta": {**pair.get("meta", {}), "judged": True}})
        else:
            set_meta(_judge_key(pair), _now(), db_path=db_path)
    return kept


# ----------------------------------------------------------- contradictions

def _find_conflicts(conn, project: str, db_path: Path) -> list[dict]:
    """Warn-zone pairs: very similar, coexisting, never superseded.

    Skips fact–fact pairs that look like sequential timeline milestones
    (deploy tips, disk free-space audits) so nightly consolidation does not
    keep re-flagging the same logging style under new SHA combinations.
    """
    from .vector import vec_search, _connect_vec  # local import: optional dep

    rows = conn.execute(
        f"""SELECT id, type, summary FROM memories
            WHERE status = 'active' AND project = ?
              AND type IN ({','.join('?' * len(CONFLICT_TYPES))})""",
        (project, *CONFLICT_TYPES),
    ).fetchall()
    ids = [r["id"] for r in rows]
    types = {r["id"]: r["type"] for r in rows}
    summaries = {r["id"]: r["summary"] for r in rows}
    if len(ids) < 2:
        return []

    # embeddings live in the vector store; read them directly
    try:
        vconn = _connect_vec(db_path)
    except Exception:
        return []
    import struct
    vecs = {}
    try:
        q = f"""SELECT memory_id, embedding FROM vec_memories
                WHERE memory_id IN ({','.join('?' * len(ids))})"""
        for r in vconn.execute(q, ids).fetchall():
            raw = r[1]
            vecs[r[0]] = struct.unpack(f"{len(raw) // 4}f", raw)
    except Exception:
        return []
    finally:
        vconn.close()

    def cos(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    existing = {(r["src_id"], r["dst_id"]) for r in conn.execute(
        "SELECT src_id, dst_id FROM memory_links WHERE kind = 'conflicts_with'"
    ).fetchall()}

    out = []
    have = [i for i in ids if i in vecs]
    for i in range(len(have)):
        for j in range(i + 1, len(have)):
            a, b = have[i], have[j]
            key = (min(a, b), max(a, b))
            if key in existing:
                continue
            if _skip_as_timeline_pair(
                types[a], types[b], summaries[a], summaries[b],
            ):
                continue
            sim = cos(vecs[a], vecs[b])
            min_sim = CONFLICT_MIN_SIM
            if types[a] == "fact" and types[b] == "fact":
                min_sim = CONFLICT_MIN_SIM_FACT
            if min_sim <= sim < CONFLICT_MAX_SIM:
                out.append({"src": key[0], "dst": key[1], "kind": "conflicts_with",
                            "weight": round(sim, 4), "directed": 0,
                            "meta": {"cos_sim": round(sim, 4), "flagged_at": _now()}})
    return out


# -------------------------------------------------------------- open loops

def _extract_loops(conn, project: str) -> list[tuple[str, str]]:
    """(line, timestamp of the session it came from) for each unfinished item."""
    rows = conn.execute(
        """SELECT content, timestamp FROM memories
           WHERE status = 'active' AND project = ?
             AND type IN ('session', 'handover')
             AND timestamp >= datetime('now', ?)
           ORDER BY timestamp DESC LIMIT 20""",
        (project, f"-{LOOP_LOOKBACK_DAYS} days"),
    ).fetchall()
    loops, seen = [], set()
    for r in rows:
        for m in _LOOP_RE.findall(r["content"] or ""):
            line = m.strip().lstrip("-*• ").strip()
            if _NEGATED_LOOP_RE.search(line):
                continue
            if 12 <= len(line) <= 300 and line.lower() not in seen:
                seen.add(line.lower())
                loops.append((line, r["timestamp"]))
    return loops[:MAX_LOOPS_PER_RUN]


def _words(text: str) -> set[str]:
    from .search import STOPWORDS
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in STOPWORDS and w not in _LOOP_MARKER_WORDS and len(w) > 1}


def _close_loops(conn, project: str) -> int:
    """Close each active loop that a later active session reports done: one
    sentence holding a done-word, no negation, and at least 60% of the loop's
    own words (never fewer than two)."""
    loops = conn.execute(
        """SELECT id, content, timestamp FROM memories
           WHERE project = ? AND type = 'open_loop' AND status = 'active'""",
        (project,)).fetchall()
    closed = 0
    for loop in loops:
        wanted = _words(loop["content"])
        if not wanted:
            continue
        later = conn.execute(
            """SELECT id, content, timestamp FROM memories
               WHERE project = ? AND type IN ('session', 'handover') AND timestamp > ?
                 AND status = 'active'
               ORDER BY timestamp ASC""",
            (project, loop["timestamp"])).fetchall()
        for session in later:
            done = False
            for sentence in re.split(r"(?<=[.!?])\s+|\n+", session["content"] or ""):
                if _NEGATION_RE.search(sentence):
                    continue
                words = _words(sentence) | set(re.findall(r"[a-z]+", sentence.lower()))
                need = max(LOOP_MIN_MATCH, LOOP_CLOSE_SHARE * len(wanted))
                if words & _DONE_WORDS and len(wanted & words) >= need:
                    done = True
                    break
            if done:
                conn.execute(
                    """UPDATE memories SET status = 'done', valid_to = ?, invalidated_by = ?
                       WHERE id = ? AND status = 'active'""",
                    (session["timestamp"], session["id"], loop["id"]))
                _audit(conn, loop["id"], "close", "consolidation", "a later session says done",
                       {"by": session["id"]})
                closed += 1
                break
    conn.commit()
    return closed


# ----------------------------------------------------------- summary repair

def _repair_summaries(db_path: Path) -> int:
    """Heal stored LLM throat-clearing ('Here is a summary of ...:') left
    by earlier prompts. Deterministic, idempotent, part of every sleep."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT id, summary FROM memories
               WHERE lower(summary) LIKE 'here%'
                  OR lower(summary) LIKE 'okay%'
                  OR lower(summary) LIKE 'sure%'"""
        ).fetchall()
        fixed = 0
        for r in rows:
            cleaned = strip_preamble(r["summary"])
            if cleaned != r["summary"]:
                conn.execute("UPDATE memories SET summary = ? WHERE id = ?",
                             (cleaned, r["id"]))
                fixed += 1
        conn.commit()
    return fixed


# --------------------------------------------------------------- the cycle

def _claim_marker(db_path: Path) -> bool:
    """Take the cross-process sleep marker in one statement: it is claimed only
    when it is empty or stale, and rowcount says who won. A check followed by
    a separate write let two processes both start."""
    from .db import connect
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(seconds=RUN_STALE_S)).isoformat()
    conn = connect(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO brain_meta (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                                              updated_at = excluded.updated_at
               WHERE brain_meta.value = '' OR brain_meta.value < ?""",
            (META_RUNNING, now.isoformat(), now.isoformat(), stale))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def _release_marker(db_path: Path) -> None:
    set_meta(META_RUNNING, "", db_path=db_path)


def _marker_is_fresh(db_path: Path) -> bool:
    since = get_meta(META_RUNNING, db_path=db_path)
    if not since:
        return False
    try:
        started = datetime.fromisoformat(since)
    except ValueError:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds() < RUN_STALE_S


async def consolidate(project: Optional[str] = None,
                      idle_days: int = 14,
                      db_path: Path = None,
                      mode: str = "full") -> dict:
    """Run one sleep cycle. Returns a plain-dict report, or
    {"skipped": "already running"} while another run is in progress.

    mode:
      - "full"  — beliefs + conflicts + loops (interactive / MCP)
      - "light" — repair + conflicts + loops; skip LLM belief
                  distillation (cheap enough for nightly auto-sleep)
    """
    db_path = db_path or DB_PATH
    if RUN_LOCK.locked():
        return {"skipped": "already running"}
    async with RUN_LOCK:
        if not _claim_marker(db_path):
            return {"skipped": "already running"}
        try:
            return await _consolidate(project, idle_days, db_path, mode)
        finally:
            _release_marker(db_path)


async def _consolidate(project: Optional[str], idle_days: int, db_path: Path,
                       mode: str) -> dict:
    from .ingest_pipeline import ingest          # late: avoids cycles
    from .linker import _write_edges, _update_degrees

    mode = (mode or "full").strip().lower()
    if mode not in ("full", "light"):
        mode = "full"
    report = {"projects": [], "decayed": 0, "started_at": _now(),
              "mode": mode,
              "summaries_repaired": _repair_summaries(db_path)}

    with _connect(db_path) as conn:
        if project:
            projects = [project]
        else:
            projects = [r["project"] for r in conn.execute(
                """SELECT project, COUNT(*) AS n FROM memories
                   WHERE status = 'active' GROUP BY project
                   HAVING n >= ?""", (MIN_CLUSTER_SIZE,)).fetchall()]

    for proj in projects:
        entry_report = {"project": proj, "beliefs": [], "conflicts": 0,
                        "loops": 0, "skipped_clusters": 0,
                        "beliefs_retired": 0, "mode": mode}
        with _connect(db_path) as conn:
            entry_report["beliefs_retired"] = _retire_bloated_beliefs(
                conn, proj, db_path)
            rows = _active_rows(conn, proj)
            rows_by_id = {r["id"]: r for r in rows}
            clusters = _clusters(conn, set(rows_by_id), rows_by_id)
            skip = [c for c in clusters if _already_believed(conn, c)]
            todo = [c for c in clusters if c not in skip]
            entry_report["skipped_clusters"] = len(skip)
            conflicts = _find_conflicts(conn, proj, db_path)
            loops = _extract_loops(conn, proj)
            # the judge reads the summary, or the opening of the text when there is none
            conflict_summaries = {r["id"]: r["summary"] or (r["content"] or "")[:400]
                                  for r in conn.execute(
                "SELECT id, summary, content FROM memories WHERE project = ? AND status = 'active'",
                (proj,))} if conflicts else {}
        conflicts = await _judge_conflicts(conflicts, conflict_summaries, db_path)

        # 1. beliefs — full mode only (light auto-sleep skips LLM cost)
        if mode == "light":
            todo = []
            entry_report["skipped_clusters"] += len(clusters) - len(skip)
        for cluster in todo:
            try:
                answer = await complete(belief_prompt(rows_by_id, cluster))
            except Exception:
                logger.warning("Consolidation: the belief prompt failed for a "
                               "cluster in %s — skipping", proj, exc_info=True)
                continue
            sentences = cited_sentences(answer, {_tag(m) for m in cluster})
            if not sentences:
                entry_report["skipped_uncited"] = entry_report.get("skipped_uncited", 0) + 1
                continue
            # A proposal waits for a human (Atlas or brain beliefs --approve):
            # only active beliefs reach the brief and search.
            belief = MemoryEntry(
                content=" ".join(sentences),
                type="belief", project=proj,
                tags=["belief", "consolidated"],
                source="consolidation",
                importance=4, status="proposed",
                writer="consolidation", trust="derived",
            )
            try:
                belief = await ingest(belief)
            except Exception:
                logger.warning("Consolidation: ingest failed for a belief "
                               "in %s — skipping", proj, exc_info=True)
                continue
            _write_edges([
                {"src": belief.id, "dst": mid, "kind": "derived_from",
                 "weight": 1.0, "directed": 1, "meta": {}}
                for mid in cluster
            ], db_path)
            # keep link_degree honest right away (sizes in the UI use it)
            try:
                _update_degrees(set(cluster) | {belief.id}, db_path)
            except Exception:
                logger.warning("Consolidation: degree refresh failed",
                               exc_info=True)
            # 2. sources are damped only when the belief is approved
            entry_report["beliefs"].append(
                {"id": belief.id, "sources": len(cluster)})

        # 3. contradictions — flagged, never auto-resolved
        if conflicts:
            _write_edges(conflicts, db_path)
            entry_report["conflicts"] = len(conflicts)

        # 4. open loops — deterministic, deduplicated; dated like their session
        for line, session_ts in loops:
            content = f"Open loop: {line}"
            if get_memory_by_content_hash(content, proj, db_path=db_path):
                continue
            try:
                when = datetime.fromisoformat(session_ts)
                when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                when = datetime.now(timezone.utc)
            loop_entry = MemoryEntry(
                content=content, summary=content,
                type="open_loop", project=proj, timestamp=when,
                tags=[LOOP_TAG], source="consolidation", importance=4,
                writer="consolidation", trust="derived",
            )
            try:
                await ingest(loop_entry)
                entry_report["loops"] += 1
            except Exception:
                logger.warning("Consolidation: loop ingest failed in %s",
                               proj, exc_info=True)

        # 5. v2.5 identity: draft a description when the project has none.
        try:
            from .workspace.identity import maybe_draft_description
            entry_report["description_drafted"] = await maybe_draft_description(proj, db_path=db_path)
        except Exception:
            logger.warning("Consolidation: description draft failed for %s", proj, exc_info=True)
            entry_report["description_drafted"] = False

        # 4b. loops a later session reports done
        with _connect(db_path) as conn:
            entry_report["loops_closed"] = _close_loops(conn, proj)

        report["projects"].append(entry_report)
        set_meta(f"consolidation_last_run:{proj}", _now(), db_path=db_path)

    # forgetting is computed at read time now; report what is past its grace
    report["decayed"] = decay_strengths(idle_days=idle_days, db_path=db_path)
    report["finished_at"] = _now()
    return report
