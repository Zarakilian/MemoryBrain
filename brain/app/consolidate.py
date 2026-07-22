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
     into tagged `note` memories so the next session starts from what is
     unfinished, not from silence. Deterministic (no LLM), deduplicated
     by content hash.

Then the whole store DECAYS: anything unrecalled for `idle_days` loses a
little strength (floored — forgetting is ranking, never deletion).

Everything here is additive and derived: no existing memory is deleted or
archived by consolidation itself (supersession of stale beliefs happens
through the same pipeline rules as everything else).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .models import MemoryEntry
from .storage import (DB_PATH, decay_strengths, get_memory_by_content_hash,
                      scale_strengths, _connect)
from .summarise import summarise, strip_preamble

logger = logging.getLogger(__name__)

# Clustering: edges at or above this combined weight bind a cluster.
CLUSTER_MIN_WEIGHT = 0.30
MIN_CLUSTER_SIZE = 3
MAX_CLUSTER_SOURCES = 12     # a belief distilled from 45 memories is mush,
                             # and its hub-degree eats the constellation:
                             # oversized components get SPLIT (tighter edge
                             # thresholds first, then time-ordered chunks)
MAX_CLUSTERS_PER_PROJECT = 5
MAX_CORPUS_CHARS = 6000
SOURCE_DAMP = 0.8            # consolidated sources sink a little

# Contradiction "warn zone": similar enough to worry, not similar enough
# to have auto-superseded. Mirrors ingest_pipeline's thresholds.
CONFLICT_MIN_SIM = 0.78
CONFLICT_MAX_SIM = 0.92
CONFLICT_TYPES = ("fact", "belief")

_LOOP_RE = re.compile(
    r"^.*(?:\bTODO\b|\bFIXME\b|\bnext session\b|\bstill need(?:s)? to\b"
    r"|\bopen question\b|\bunresolved\b|\bfollow[- ]up\b).*$",
    re.IGNORECASE | re.MULTILINE)
LOOP_TAG = "open-loop"
LOOP_LOOKBACK_DAYS = 30
MAX_LOOPS_PER_RUN = 12


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
        archive_memory(r["id"], superseded_by=None, db_path=db_path)
        try:
            from .vector import vec_update_metadata
            vec_update_metadata(r["id"], {"status": "archived"})
        except Exception:
            pass
    return len(rows)


def _corpus(rows_by_id: dict, cluster: list[str]) -> str:
    parts = []
    for mid in sorted(cluster, key=lambda m: rows_by_id[m]["timestamp"]):
        r = rows_by_id[mid]
        text = (r["summary"] or r["content"] or "").strip()
        parts.append(f"[{(r['timestamp'] or '')[:10]} {r['type']}] {text}")
    corpus = "\n".join(parts)
    return corpus[:MAX_CORPUS_CHARS]


def _already_believed(conn, cluster: list[str]) -> bool:
    """True if an ACTIVE belief already derives from (most of) this cluster —
    re-synthesising it would just churn tokens."""
    rows = conn.execute(
        f"""SELECT l.src_id, COUNT(*) AS n FROM memory_links l
            JOIN memories b ON b.id = l.src_id
            WHERE l.kind = 'derived_from' AND b.status = 'active'
              AND l.dst_id IN ({','.join('?' * len(cluster))})
            GROUP BY l.src_id""",
        cluster,
    ).fetchall()
    best = max((r["n"] for r in rows), default=0)
    return best >= len(cluster)          # identical coverage → nothing new


# ----------------------------------------------------------- contradictions

def _find_conflicts(conn, project: str, db_path: Path) -> list[dict]:
    """Warn-zone pairs: very similar, coexisting, never superseded."""
    from .vector import vec_search, _connect_vec  # local import: optional dep

    rows = conn.execute(
        f"""SELECT id, type FROM memories
            WHERE status = 'active' AND project = ?
              AND type IN ({','.join('?' * len(CONFLICT_TYPES))})""",
        (project, *CONFLICT_TYPES),
    ).fetchall()
    ids = [r["id"] for r in rows]
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
            sim = cos(vecs[a], vecs[b])
            if CONFLICT_MIN_SIM <= sim < CONFLICT_MAX_SIM:
                out.append({"src": key[0], "dst": key[1], "kind": "conflicts_with",
                            "weight": round(sim, 4), "directed": 0,
                            "meta": {"cos_sim": round(sim, 4), "flagged_at": _now()}})
    return out


# -------------------------------------------------------------- open loops

def _extract_loops(conn, project: str) -> list[str]:
    rows = conn.execute(
        """SELECT content FROM memories
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
            if 12 <= len(line) <= 300 and line.lower() not in seen:
                seen.add(line.lower())
                loops.append(line)
    return loops[:MAX_LOOPS_PER_RUN]


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

async def consolidate(project: Optional[str] = None,
                      idle_days: int = 14,
                      db_path: Path = None,
                      mode: str = "full") -> dict:
    """Run one sleep cycle. Returns a plain-dict report.

    mode:
      - "full"  — beliefs + conflicts + loops + decay (interactive / MCP)
      - "light" — repair + conflicts + loops + decay; skip LLM belief
                  distillation (cheap enough for nightly auto-sleep)
    """
    from .ingest_pipeline import ingest          # late: avoids cycles
    from .linker import _write_edges, _update_degrees

    db_path = db_path or DB_PATH
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

        # 1. beliefs — full mode only (light auto-sleep skips LLM cost)
        if mode == "light":
            todo = []
            entry_report["skipped_clusters"] += len(clusters) - len(skip)
        for cluster in todo:
            corpus = _corpus(rows_by_id, cluster)
            prompt = ("Distil these related memories into their single current "
                      "truth. State the present state of affairs, decisions "
                      "that stand, and how things work now — not the history "
                      "of getting there:\n\n" + corpus)
            if len(prompt) <= 420:
                # providers return short inputs verbatim (instruction and
                # all) — a tiny cluster is its own distillation
                text = corpus
            else:
                try:
                    text = await summarise(prompt, max_sentences=6)
                except Exception:
                    logger.warning("Consolidation: summarise failed for a "
                                   "cluster in %s — skipping", proj,
                                   exc_info=True)
                    continue
            belief = MemoryEntry(
                content=text.strip(),
                type="belief", project=proj,
                tags=["belief", "consolidated"],
                source="consolidation",
                importance=4,
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
            # 2. the belief speaks first for its sources now
            scale_strengths(cluster, SOURCE_DAMP, db_path=db_path)
            entry_report["beliefs"].append(
                {"id": belief.id, "sources": len(cluster)})

        # 3. contradictions — flagged, never auto-resolved
        if conflicts:
            _write_edges(conflicts, db_path)
            entry_report["conflicts"] = len(conflicts)

        # 4. open loops — deterministic, deduplicated
        for line in loops:
            content = f"Open loop: {line}"
            if get_memory_by_content_hash(content, proj, db_path=db_path):
                continue
            loop_entry = MemoryEntry(
                content=content, summary=content,
                type="note", project=proj,
                tags=[LOOP_TAG], source="consolidation", importance=4,
            )
            try:
                await ingest(loop_entry)
                entry_report["loops"] += 1
            except Exception:
                logger.warning("Consolidation: loop ingest failed in %s",
                               proj, exc_info=True)

        report["projects"].append(entry_report)

    # the whole store forgets a little, gracefully
    report["decayed"] = decay_strengths(idle_days=idle_days, db_path=db_path)
    report["finished_at"] = _now()
    return report
