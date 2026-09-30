"""Measure MemoryBrain's own search quality.

recall@k: share of a question's relevant memories found in the top k.
MRR (mean reciprocal rank): 1/rank of the first relevant hit, averaged.
A labels file is JSONL, one question per line:
    {"query": str, "project": str | null, "relevant": [memory ids]}
`brain eval` builds one from real searches; synthetic_fixture() is a neutral
stand-in so tests and the Task 9 comparison need no real data.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Callable, Iterable, Optional


def recall_at_k(ranked_ids: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked_ids[:k]) & set(relevant)) / len(relevant)


def reciprocal_rank(ranked_ids: list[str], relevant: set[str]) -> float:
    for rank, memory_id in enumerate(ranked_ids, start=1):
        if memory_id in relevant:
            return 1.0 / rank
    return 0.0


def score_run(results: dict[str, list[str]], labels: dict[str, set[str]],
              ks: Iterable[int] = (5, 10)) -> dict:
    """Average recall@k and MRR over labelled questions, with a row per question."""
    ks = tuple(ks)
    rows = []
    for query, relevant in labels.items():
        ranked = results.get(query, [])
        row = {"query": query, "relevant": sorted(relevant), "ranked": ranked[:max(ks)],
               "rr": reciprocal_rank(ranked, relevant)}
        for k in ks:
            row[f"recall@{k}"] = recall_at_k(ranked, relevant, k)
        rows.append(row)
    n = len(rows)
    report: dict = {"queries": n}
    for k in ks:
        report[f"recall@{k}"] = round(sum(r[f"recall@{k}"] for r in rows) / n, 4) if n else 0.0
    report["mrr"] = round(sum(r["rr"] for r in rows) / n, 4) if n else 0.0
    report["per_query"] = rows
    return report


def read_labels(labels_path) -> list[dict]:
    lines = Path(labels_path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _key(query: str, project: Optional[str]) -> str:
    return f"{project}: {query}" if project else query


async def run_labels(labels_path, search_fn: Callable, ks: Iterable[int] = (5, 10)) -> dict:
    """Run every labelled question through search_fn(query, project) and score it.
    search_fn may be sync or async and returns result dicts carrying "id"."""
    results: dict[str, list[str]] = {}
    labels: dict[str, set[str]] = {}
    for item in read_labels(labels_path):
        query, project = item["query"], item.get("project")
        hits = search_fn(query, project)
        if inspect.isawaitable(hits):
            hits = await hits
        key = _key(query, project)
        results[key] = [h["id"] for h in hits if isinstance(h, dict) and "id" in h]
        labels[key] = set(item.get("relevant") or [])
    return score_run(results, labels, ks)


# ------------------------------------------------------------- synthetic fixture

_BILLING = [
    ("fact", "The monthly invoice export runs at 02:00 UTC on the first day of each month "
             "and writes one CSV per customer."),
    ("decision", "Decision: invoice totals are rounded to two decimals only at the final total, "
                 "never per line item."),
    ("note", "Ticket INC-4821: duplicate invoices went out to Northwind because the retry job "
             "ignored the sent flag."),
    ("fact", "The export script lives at C:\\work\\repos\\Daily Reports\\Tools\\ReportFlow\\"
             "export_invoices.py and is started by the scheduler."),
    ("note", "Customers on the annual plan are billed in advance; monthly customers are billed "
             "in arrears."),
    ("open_loop", "Add a guard so the retry job skips invoices already marked as sent."),
    ("fact", "Tax rates are loaded from rates.yaml and cached for 24 hours."),
    ("note", "Refunds over 500 need a second approver in the finance team."),
    ("decision", "Decision: credit notes reuse the original invoice number with a -CN suffix."),
]
_INFRA = [
    ("fact", "The primary database runs on SRVDB01; the replica is SRVDB02 in the second rack."),
    ("fact", "Backups are written nightly to the NAS share and kept for 35 days."),
    ("note", "Rotating the TLS certificate on the load balancer takes about ten minutes of "
             "planned downtime."),
    ("decision", "Decision: all services log in JSON so the log shipper can parse them "
                 "without regular expressions."),
    ("fact", "Disk usage alerts fire at 85 percent on the data volume."),
    ("note", "WORK-PC must connect to the VPN before it can reach wiki.example.com."),
    ("open_loop", "Move the log shipper to the new collector before the old one is retired."),
    ("fact", "Port 7741 is only bound to 127.0.0.1 on developer machines."),
    ("note", "The staging cluster restarts every Sunday at 04:00 for patching."),
    ("fact", "Ticket CHG-1177 covers the database upgrade to the new major version."),
]
_APP = [
    ("fact", "The mobile app caches the product catalogue for six hours."),
    ("decision", "Decision: the checkout screen keeps a single primary button."),
    ("note", "Push notifications are sent through the queue worker, never directly from the API."),
    ("fact", "The feature flag new-search is enabled for ten percent of users."),
    ("note", "Crash reports show the image picker fails on older tablets with little memory."),
    ("fact", "Release builds are signed in the pipeline, never on developer laptops."),
    ("open_loop", "Do an accessibility pass on the settings screen."),
    ("fact", "The API rate limit is 60 requests per minute per user token."),
    ("note", "Dark mode colours come from the shared design tokens file."),
    ("session", "Session: slow app startup was traced to the analytics SDK starting on the main "
                "thread; it now starts on a background task."),
]
_FILLER = [
    "We checked the queue depth and the worker logs for the billing run.",
    "The ledger snapshot times looked normal for the previous three months.",
    "Customer support reported that several accounts saw empty statements.",
    "We compared the export files with the ones from last quarter.",
    "Nothing in the deployment history changed the export job itself.",
    "The scheduler host had been patched during the maintenance window.",
]


def _long_session() -> str:
    parts, i = ["Session: investigation of the empty invoice export."], 0
    while len(" ".join(parts)) < 2100:
        parts.append(_FILLER[i % len(_FILLER)])
        i += 1
    parts.append("The root cause was a daylight saving shift that moved the cron job to "
                 "01:00, so the export read an empty ledger snapshot.")
    parts.append("We pinned the job to UTC and re-ran the export for all customers.")
    return " ".join(parts)


def synthetic_fixture() -> tuple[list[dict], list[dict]]:
    """(memories, labels): 30 neutral memories over 3 made-up projects and 20
    labelled questions (exact tokens, plain questions, an answer past character
    2,000 of a long session, one question across all projects)."""
    memories: list[dict] = []
    for project, rows in (("acme-billing", _BILLING), ("acme-infra", _INFRA),
                          ("northwind-app", _APP)):
        for mtype, content in rows:
            memories.append({"id": f"syn-{len(memories) + 1:02d}", "project": project,
                             "type": mtype, "content": content})
        if project == "acme-billing":
            memories.append({"id": f"syn-{len(memories) + 1:02d}", "project": project,
                             "type": "session", "content": _long_session()})
    by_text = {m["content"][:40]: m["id"] for m in memories}

    def ids(*prefixes):
        return [next(v for k, v in by_text.items() if k.startswith(p)) for p in prefixes]

    q = [
        ("SRVDB01", "acme-infra", ids("The primary database")),
        ("INC-4821", "acme-billing", ids("Ticket INC-4821")),
        ("export_invoices.py", "acme-billing", ids("The export script")),
        ("CHG-1177", "acme-infra", ids("Ticket CHG-1177")),
        ("when does the monthly invoice export run", "acme-billing", ids("The monthly invoice")),
        ("how are invoice totals rounded", "acme-billing", ids("Decision: invoice totals")),
        ("what happens with refunds above five hundred", "acme-billing", ids("Refunds over 500")),
        ("how long are backups kept", "acme-infra", ids("Backups are written")),
        ("how do services format their logs", "acme-infra", ids("Decision: all services")),
        ("when do disk alerts fire", "acme-infra", ids("Disk usage alerts")),
        ("how does the app send push notifications", "northwind-app", ids("Push notifications")),
        ("how long is the product catalogue cached", "northwind-app", ids("The mobile app caches")),
        ("where are release builds signed", "northwind-app", ids("Release builds are signed")),
        ("what is the api rate limit", "northwind-app", ids("The API rate limit")),
        ("why was app startup slow", "northwind-app", ids("Session: slow app startup")),
        ("naming rule for credit notes", "acme-billing", ids("Decision: credit notes")),
        ("which share of users get the new search", "northwind-app", ids("The feature flag")),
        ("what was the root cause of the empty invoice export", "acme-billing",
         ids("Session: investigation")),
        ("daylight saving cron shift", "acme-billing", ids("Session: investigation")),
        ("restart schedule for the staging cluster", None, ids("The staging cluster")),
    ]
    labels = [{"query": query, "project": project, "relevant": relevant}
              for query, project, relevant in q]
    return memories, labels
