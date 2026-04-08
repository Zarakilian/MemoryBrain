import os
import re
import logging
from datetime import datetime
from typing import Optional

import httpx

from ...models import MemoryEntry
from ...storage import get_memory_by_source, DB_PATH

logger = logging.getLogger(__name__)

REQUIRED_ENV = ["CONFLUENCE_URL", "CONFLUENCE_TOKEN"]
SCHEDULE_HOURS = 6
MEMORY_TYPE = "confluence"


def _confluence_url() -> str:
    return os.getenv("CONFLUENCE_URL", "").rstrip("/")


def _token() -> str:
    return os.getenv("CONFLUENCE_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = (text
            .replace("&nbsp;", " ").replace("&lt;", "<")
            .replace("&gt;", ">").replace("&amp;", "&")
            .replace("&quot;", '"'))
    return re.sub(r"\s+", " ", text).strip()


async def health_check() -> bool:
    url = _confluence_url()
    token = _token()
    if not url or not token:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{url}/rest/api/user/current",
                headers=_headers(),
                timeout=10,
            )
        return resp.status_code == 200
    except Exception:
        return False


async def ingest(since: datetime) -> list[MemoryEntry]:
    """Pull pages where current user is contributor, updated since `since`."""
    url = _confluence_url()
    since_str = since.strftime("%Y-%m-%d")
    cql = f'lastModified > "{since_str}" AND contributor = currentUser()'
    entries = []

    async with httpx.AsyncClient() as client:
        start = 0
        while True:
            resp = await client.get(
                f"{url}/rest/api/content/search",
                headers=_headers(),
                params={
                    "cql": cql,
                    "expand": "body.storage,version,space",
                    "limit": 50,
                    "start": start,
                },
                timeout=30,
            )
            if resp.status_code != 200:
                logger.warning(f"Confluence search returned {resp.status_code}")
                break

            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            for page in results:
                page_id = page["id"]
                space_key = page["space"]["key"].lower()
                title = page["title"]
                last_modified_str = page["version"]["when"]
                web_path = page["_links"]["webui"]
                page_url = f"{url}{web_path}"
                html_body = page.get("body", {}).get("storage", {}).get("value", "")
                plain_text = _strip_html(html_body)[:8000]

                # Deduplication: skip if we already have this page at same/newer version
                existing = get_memory_by_source(page_url, db_path=DB_PATH)
                if existing is not None:
                    try:
                        page_dt = datetime.fromisoformat(
                            last_modified_str.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        if existing.timestamp >= page_dt:
                            logger.debug(f"Skipping unchanged page: {title}")
                            continue
                    except ValueError:
                        pass  # can't parse date — re-ingest

                entry = MemoryEntry(
                    content=f"{title}\n\n{plain_text}",
                    type=MEMORY_TYPE,
                    project=space_key,
                    tags=[space_key, page_id],
                    source=page_url,
                )
                entries.append(entry)

            # Pagination
            if "next" not in data.get("_links", {}):
                break
            if len(results) < 50:
                break
            start += 50

    logger.info(f"Confluence: {len(entries)} new/updated pages to ingest")
    return entries
