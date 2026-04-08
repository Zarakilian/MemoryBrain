"""
Jira Plugin — STUB (not active)

When implemented, this plugin would pull Jira tickets assigned to you
or recently updated, storing title + description as memories.

To activate: rename to jira.py, implement health_check and ingest,
add JIRA_URL and JIRA_TOKEN to .env.
"""
from datetime import datetime
from ...models import MemoryEntry

REQUIRED_ENV = ["JIRA_URL", "JIRA_TOKEN"]
SCHEDULE_HOURS = 6
MEMORY_TYPE = "jira"


async def health_check() -> bool:
    raise NotImplementedError("Jira plugin is a stub — not yet implemented")


async def ingest(since: datetime) -> list[MemoryEntry]:
    raise NotImplementedError("Jira plugin is a stub — not yet implemented")
