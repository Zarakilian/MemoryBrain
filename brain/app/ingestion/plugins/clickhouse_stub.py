"""
ClickHouse Plugin — STUB (not active)

When implemented, this plugin would pull query results or metrics
flagged manually from ClickHouse APM / observability data.

To activate: rename to clickhouse.py, implement health_check and ingest,
add CLICKHOUSE_IOM_URL and CLICKHOUSE_TOKEN to .env.

Plugin contract requires:
    REQUIRED_ENV: list[str]
    SCHEDULE_HOURS: int
    MEMORY_TYPE: str
    async def health_check() -> bool
    async def ingest(since: datetime) -> list[MemoryEntry]
"""
from datetime import datetime
from ...models import MemoryEntry

REQUIRED_ENV = ["CLICKHOUSE_IOM_URL", "CLICKHOUSE_TOKEN"]
SCHEDULE_HOURS = 12
MEMORY_TYPE = "clickhouse"


async def health_check() -> bool:
    raise NotImplementedError("ClickHouse plugin is a stub — not yet implemented")


async def ingest(since: datetime) -> list[MemoryEntry]:
    raise NotImplementedError("ClickHouse plugin is a stub — not yet implemented")
