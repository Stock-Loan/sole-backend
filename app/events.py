import asyncio
import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError

from app.core.settings import settings
from app.db.session import engine
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
            except SQLAlchemyError:
                logger.exception("Failed to refresh PBGC mid-term rates")
    finally:
        if task:
            _running_tasks.discard(task)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info("Application startup")
    _shutdown_event.clear()
    global _scheduler
    if settings.pbgc_rate_scrape_enabled:
        _scheduler = AsyncIOScheduler(timezone="UTC")
        trigger = CronTrigger(
            hour=settings.pbgc_rate_scrape_hour,
            minute=settings.pbgc_rate_scrape_minute,
        )
        _scheduler.add_job(_run_pbgc_scrape, trigger=trigger, id="pbgc_rate_scrape")
        _scheduler.start()
        await _run_pbgc_scrape()
    try:
        yield
    finally:
        logger.info("Application shutdown")
        _shutdown_event.set()
        if _scheduler:
            _scheduler.shutdown(wait=False)
            _scheduler = None
        if _running_tasks:
            for task in list(_running_tasks):
                task.cancel()
            await asyncio.gather(*_running_tasks, return_exceptions=True)
        await engine.dispose()


def register_event_handlers(app: FastAPI) -> None:
    app.router.lifespan_context = lifespan
