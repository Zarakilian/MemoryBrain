"""Background auto-sleep — light consolidation on a daily schedule.

Enabled with MEMORYBRAIN_AUTO_CONSOLIDATE=true (default false).
Runs once per UTC day after MEMORYBRAIN_CONSOLIDATE_HOUR (default 3).

Light mode (default for auto runs):
  - summary repair, conflict flagging, open-loop extract, strength decay
  - skips expensive LLM belief distillation unless
    MEMORYBRAIN_AUTO_CONSOLIDATE_FULL=true

Only projects with activity in the last MEMORYBRAIN_AUTO_ACTIVE_DAYS days
(default 14) are touched — idle archives sleep undisturbed.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .storage import DB_PATH, _connect, get_meta, set_meta

logger = logging.getLogger(__name__)

META_LAST_RUN = "auto_consolidate_last_run"
META_LAST_REPORT = "auto_consolidate_last_report"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def auto_consolidate_enabled() -> bool:
    return _env_bool("MEMORYBRAIN_AUTO_CONSOLIDATE", False)


def scheduler_status(db_path: Path = DB_PATH) -> dict:
    return {
        "enabled": auto_consolidate_enabled(),
        "hour_utc": _env_int("MEMORYBRAIN_CONSOLIDATE_HOUR", 3),
        "full": _env_bool("MEMORYBRAIN_AUTO_CONSOLIDATE_FULL", False),
        "active_days": _env_int("MEMORYBRAIN_AUTO_ACTIVE_DAYS", 14),
        "idle_days": _env_int("MEMORYBRAIN_AUTO_IDLE_DAYS", 14),
        "last_run": get_meta(META_LAST_RUN, db_path=db_path),
        "last_report": get_meta(META_LAST_REPORT, db_path=db_path),
    }


def _active_projects(days: int, db_path: Path) -> list[str]:
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    # iso compare works for our timestamps
    from datetime import timedelta
    iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _connect(db_path) as conn:
        rows = conn.execute(
            """SELECT project FROM memories
               WHERE status = 'active' AND timestamp >= ?
               GROUP BY project""",
            (iso,),
        ).fetchall()
    return [r["project"] for r in rows]


async def run_auto_consolidate(db_path: Path = DB_PATH,
                               force: bool = False) -> dict:
    """Run one auto-sleep cycle if due (or force=True)."""
    from .consolidate import consolidate

    if not force and not auto_consolidate_enabled():
        return {"skipped": True, "reason": "disabled"}

    hour = max(0, min(_env_int("MEMORYBRAIN_CONSOLIDATE_HOUR", 3), 23))
    now = datetime.now(timezone.utc)
    last = get_meta(META_LAST_RUN, db_path=db_path)
    if last and not force:
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (now - last_dt).total_seconds() < 20 * 3600:
                return {"skipped": True, "reason": "ran_recently", "last_run": last}
        except ValueError:
            pass
        # only after the configured hour UTC, once per calendar day
        if now.hour < hour and last[:10] == now.date().isoformat():
            return {"skipped": True, "reason": "before_hour", "hour_utc": hour}
        if last[:10] == now.date().isoformat():
            return {"skipped": True, "reason": "already_today", "last_run": last}

    active_days = _env_int("MEMORYBRAIN_AUTO_ACTIVE_DAYS", 14)
    idle_days = _env_int("MEMORYBRAIN_AUTO_IDLE_DAYS", 14)
    full = _env_bool("MEMORYBRAIN_AUTO_CONSOLIDATE_FULL", False)
    mode = "full" if full else "light"
    projects = _active_projects(active_days, db_path)
    if not projects:
        report = {"skipped": True, "reason": "no_active_projects",
                  "mode": mode, "started_at": now.isoformat()}
        set_meta(META_LAST_RUN, now.isoformat(), db_path=db_path)
        set_meta(META_LAST_REPORT, str(report), db_path=db_path)
        return report

    reports = []
    for proj in projects:
        try:
            r = await consolidate(project=proj, idle_days=idle_days,
                                  mode=mode, db_path=db_path)
            reports.append(r)
        except Exception as e:
            logger.exception("auto-consolidate failed for %s", proj)
            reports.append({"project": proj, "error": str(e)})

    finished = datetime.now(timezone.utc).isoformat()
    summary = {
        "mode": mode,
        "projects": projects,
        "reports": reports,
        "started_at": now.isoformat(),
        "finished_at": finished,
    }
    set_meta(META_LAST_RUN, finished, db_path=db_path)
    # store compact JSON-ish string (truncated)
    import json
    set_meta(META_LAST_REPORT, json.dumps(summary, default=str)[:8000],
             db_path=db_path)
    logger.info("auto-consolidate %s finished for %d projects",
                mode, len(projects))
    return summary


async def scheduler_loop(stop_event: asyncio.Event,
                         db_path: Path = DB_PATH) -> None:
    """Poll every 15 minutes; run auto-sleep when due."""
    logger.info("auto-consolidate scheduler started (enabled=%s)",
                auto_consolidate_enabled())
    while not stop_event.is_set():
        try:
            if auto_consolidate_enabled():
                await run_auto_consolidate(db_path=db_path)
        except Exception:
            logger.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=900)
        except asyncio.TimeoutError:
            pass
    logger.info("auto-consolidate scheduler stopped")
