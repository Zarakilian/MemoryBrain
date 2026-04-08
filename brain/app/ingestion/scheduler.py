import logging
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from ..storage import get_last_run, set_last_run, DB_PATH
from ..ingest_pipeline import ingest as ingest_pipeline_ingest

logger = logging.getLogger(__name__)


async def run_plugin(plugin) -> None:
    """Run one plugin cycle: determine since, call ingest, store each result, update last_run."""
    name = plugin.MEMORY_TYPE
    last_run = get_last_run(name, db_path=DB_PATH)

    if last_run is None:
        since = datetime.now(timezone.utc) - timedelta(hours=plugin.SCHEDULE_HOURS)
        logger.info(f"Plugin '{name}': first run, pulling last {plugin.SCHEDULE_HOURS}h")
    else:
        since = last_run
        logger.info(f"Plugin '{name}': pulling since {since.isoformat()}")

    try:
        entries = await plugin.ingest(since)
        logger.info(f"Plugin '{name}': got {len(entries)} entries")
        for entry in entries:
            await ingest_pipeline_ingest(entry)
        set_last_run(name, datetime.now(timezone.utc), db_path=DB_PATH)
    except Exception as e:
        logger.error(f"Plugin '{name}' run failed: {e}")
        # Do not update last_run on failure — retry from same point next cycle


def start_scheduler(active_plugins: list) -> AsyncIOScheduler:
    """Create and start the APScheduler. Registers one job per active plugin."""
    scheduler = AsyncIOScheduler()

    for plugin in active_plugins:
        scheduler.add_job(
            run_plugin,
            trigger="interval",
            hours=plugin.SCHEDULE_HOURS,
            args=[plugin],
            id=plugin.MEMORY_TYPE,
            replace_existing=True,
        )
        logger.info(f"Scheduled plugin '{plugin.MEMORY_TYPE}' every {plugin.SCHEDULE_HOURS}h")

    scheduler.start()
    return scheduler
