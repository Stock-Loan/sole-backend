import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.core.settings import settings
from app.db.session import AsyncSessionLocal
from app.services import pbgc_rates

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_shutdown_event = asyncio.Event()
_running_tasks: set[asyncio.Task] = set()


def is_shutting_down() -> bool:
    return _shutdown_event.is_set()


async def _run_pbgc_scrape() -> None:
    task = asyncio.current_task()
    if task:
        _running_tasks.add(task)
    try:
        if _shutdown_event.is_set():
            return
        async with AsyncSessionLocal() as db:
            try:
                if not pbgc_rates.should_run_scrape():
                    if await pbgc_rates.has_any_rates(db):
                        return
                if _shutdown_event.is_set():
                    return
                await pbgc_rates.upsert_current_year_rates(db)
            except asyncio.CancelledError:
                logger.info("PBGC rate refresh cancelled")
                raise
            except Exception:
                logger.exception("Failed to refresh PBGC mid-term rates")
    finally:
        if task:
            _running_tasks.discard(task)


def register_event_handlers(app: FastAPI) -> None:
    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("Application startup")
        if settings.pbgc_rate_scrape_enabled:
            global _scheduler
            _scheduler = AsyncIOScheduler(timezone="UTC")
            trigger = CronTrigger(
                hour=settings.pbgc_rate_scrape_hour,
                minute=settings.pbgc_rate_scrape_minute,
            )
            _scheduler.add_job(_run_pbgc_scrape, trigger=trigger, id="pbgc_rate_scrape")
            _scheduler.start()
            await _run_pbgc_scrape()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        logger.info("Application shutdown")
        _shutdown_event.set()
        if _scheduler:
            _scheduler.shutdown(wait=False)
        if _running_tasks:
            for task in list(_running_tasks):
                task.cancel()
            await asyncio.gather(*_running_tasks, return_exceptions=True)
