"""
ClickHouse APM Plugin

Pulls error rate + P95 latency summaries per (service, operator) from
apm.otel_traces_local on the IOM ClickHouse cluster.

One memory entry per (service, operator) pair with activity since `since`.
Deduplicates by source URL = clickhouse://<service>@<operator>/<window>.

Plugin contract:
    REQUIRED_ENV: list[str]
    SCHEDULE_HOURS: int
    MEMORY_TYPE: str
    async def health_check() -> bool
    async def ingest(since: datetime) -> list[MemoryEntry]
"""
import os
import json
import logging
from datetime import datetime, timezone

import httpx

from ...models import MemoryEntry
from ...storage import get_memory_by_source, DB_PATH

logger = logging.getLogger(__name__)

REQUIRED_ENV = ["CLICKHOUSE_IOM_URL", "CLICKHOUSE_TOKEN"]
SCHEDULE_HOURS = 12
MEMORY_TYPE = "clickhouse"

# Minimum spans in window to emit a memory — filters out noise
MIN_SPANS = 50

_HEALTH_QUERY = "SELECT 1"

_APM_QUERY = """
SELECT
    ServiceName                          AS service,
    SpanAttributes['server.address']     AS operator,
    count()                              AS total_spans,
    countIf(StatusCode = 'Error')        AS error_count,
    round(countIf(StatusCode = 'Error') / count() * 100, 2) AS error_rate_pct,
    round(quantile(0.95)(Duration) / 1e6, 1) AS p95_ms
FROM apm.otel_traces_local
WHERE
    StartTime >= '{since}'
    AND StartTime <  '{until}'
    AND SpanAttributes['server.address'] != ''
    AND ServiceName != ''
GROUP BY service, operator
HAVING total_spans >= {min_spans}
ORDER BY error_rate_pct DESC, total_spans DESC
LIMIT 100
FORMAT JSONEachRow
"""


def _ch_url() -> str:
    return os.getenv("CLICKHOUSE_IOM_URL", "").rstrip("/")


def _token() -> str:
    return os.getenv("CLICKHOUSE_TOKEN", "")


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


async def _query(sql: str) -> httpx.Response:
    async with httpx.AsyncClient() as client:
        return await client.post(
            _ch_url(),
            content=sql.encode(),
            headers=_headers(),
            timeout=30,
        )


async def health_check() -> bool:
    url = _ch_url()
    token = _token()
    if not url or not token:
        return False
    try:
        resp = await _query(_HEALTH_QUERY)
        return resp.status_code == 200
    except Exception:
        return False


def _importance_from_error_rate(error_rate_pct: float) -> int:
    """Map error rate % to importance score."""
    if error_rate_pct >= 5.0:
        return 5   # critical
    elif error_rate_pct >= 2.0:
        return 4   # important
    elif error_rate_pct >= 0.5:
        return 3   # notable
    else:
        return 2   # low noise, store for reference


def _source_key(service: str, operator: str, window_start: str) -> str:
    """Stable dedup key: clickhouse://<service>@<operator>/<window>"""
    safe_svc = service.replace("/", "_")
    safe_op = operator.replace("/", "_")
    safe_win = window_start.replace(":", "-").replace(" ", "T")[:13]  # YYYY-MM-DDTHH
    return f"clickhouse://{safe_svc}@{safe_op}/{safe_win}"


async def ingest(since: datetime) -> list[MemoryEntry]:
    """Pull APM health snapshot for all active services since `since`."""
    # Ensure timezone-aware
    if since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    until = datetime.now(timezone.utc)

    sql = _APM_QUERY.format(
        since=since.strftime("%Y-%m-%d %H:%M:%S"),
        until=until.strftime("%Y-%m-%d %H:%M:%S"),
        min_spans=MIN_SPANS,
    )

    try:
        resp = await _query(sql)
    except Exception as e:
        logger.error(f"ClickHouse query failed: {e}")
        return []

    if resp.status_code != 200:
        logger.warning(f"ClickHouse returned {resp.status_code}: {resp.text[:200]}")
        return []

    entries = []
    window_label = since.strftime("%Y-%m-%d %H:%M")

    for line in resp.text.strip().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue

        service = row.get("service", "")
        operator = row.get("operator", "")
        if not service or not operator:
            continue

        total = int(row.get("total_spans", 0))
        errors = int(row.get("error_count", 0))
        error_rate = float(row.get("error_rate_pct", 0.0))
        p95 = float(row.get("p95_ms", 0.0))

        source = _source_key(service, operator, window_label)

        # Deduplication: skip if this window snapshot already stored
        if get_memory_by_source(source, db_path=DB_PATH) is not None:
            continue

        content = (
            f"APM snapshot [{window_label}] {service} on {operator}: "
            f"{total:,} spans, {errors:,} errors ({error_rate:.2f}% error rate), "
            f"P95 latency {p95:.0f}ms"
        )

        importance = _importance_from_error_rate(error_rate)

        entry = MemoryEntry(
            content=content,
            summary=content,  # already concise — skip Ollama
            type=MEMORY_TYPE,
            project=service,
            tags=[service, operator, "apm"],
            source=source,
            importance=importance,
        )
        entries.append(entry)

    logger.info(f"ClickHouse: {len(entries)} new APM snapshots to ingest")
    return entries
