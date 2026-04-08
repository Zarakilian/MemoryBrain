import os
import logging
from datetime import datetime
from typing import Optional

import httpx

from ...models import MemoryEntry
from ...storage import get_memory_by_source, DB_PATH

logger = logging.getLogger(__name__)

REQUIRED_ENV = ["PAGERDUTY_TOKEN"]
SCHEDULE_HOURS = 2
MEMORY_TYPE = "pagerduty"

PD_BASE = "https://api.pagerduty.com"
_cached_user_id: Optional[str] = None


def _token() -> str:
    return os.getenv("PAGERDUTY_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Token token={_token()}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }


async def health_check() -> bool:
    if not _token():
        return False
    global _cached_user_id
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{PD_BASE}/users/me",
                headers=_headers(),
                timeout=10,
            )
        if resp.status_code == 200:
            _cached_user_id = resp.json()["user"]["id"]
            return True
        return False
    except Exception:
        return False


def _duration_minutes(created_at: str, resolved_at: str) -> int:
    try:
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        created = datetime.strptime(created_at, fmt)
        resolved = datetime.strptime(resolved_at, fmt)
        return max(0, int((resolved - created).total_seconds() / 60))
    except Exception:
        return 0


async def ingest(since: datetime) -> list[MemoryEntry]:
    """Pull incidents resolved since `since` assigned to current user."""
    global _cached_user_id

    # Always refresh user ID at ingest time to ensure fresh auth
    await health_check()
    if not _cached_user_id:
        logger.warning("PagerDuty: cannot determine current user ID, skipping")
        return []

    entries = []
    offset = 0

    async with httpx.AsyncClient() as client:
        while True:
            resp = await client.get(
                f"{PD_BASE}/incidents",
                headers=_headers(),
                params={
                    "statuses[]": "resolved",
                    "since": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "assigned_to_user[]": _cached_user_id,
                    "limit": 100,
                    "offset": offset,
                },
                timeout=30,
            )

            if resp.status_code != 200:
                logger.warning(f"PagerDuty incidents API returned {resp.status_code}")
                break

            data = resp.json()
            incidents = data.get("incidents", [])
            if not incidents:
                break

            for inc in incidents:
                incident_url = inc["html_url"]

                # Deduplication: skip if already stored
                if get_memory_by_source(incident_url, db_path=DB_PATH) is not None:
                    continue

                duration = _duration_minutes(inc["created_at"], inc.get("resolved_at", ""))
                resolved_at = inc.get("resolved_at", "")[:16].replace("T", " ")
                service = inc.get("service", {}).get("summary", "unknown service")
                urgency = inc.get("urgency", "unknown")

                content = (
                    f"[{urgency}] {inc['title']} — {service} — "
                    f"resolved in {duration}m ({resolved_at})"
                )

                entry = MemoryEntry(
                    content=content,
                    summary=content,   # no Ollama needed — already concise
                    type=MEMORY_TYPE,
                    project="pagerduty",
                    tags=[service, urgency, inc["id"]],
                    source=incident_url,
                    importance=4,
                )
                entries.append(entry)

            if not data.get("more", False):
                break
            offset += 100

    logger.info(f"PagerDuty: {len(entries)} new incidents to ingest")
    return entries
