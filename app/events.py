import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

from app.core.settings import settings
from app.db.session import AsyncSessionLocal
from app.services import pbgc_rates

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_pbgc_scrape() -> None:
    async with AsyncSessionLocal() as db:
        try:
            if not pbgc_rates.should_run_scrape():
                if await pbgc_rates.has_any_rates(db):
                    return
            await pbgc_rates.upsert_current_year_rates(db)
        except Exception:
            logger.exception("Failed to refresh PBGC mid-term rates")


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
        if _scheduler:
            _scheduler.shutdown(wait=False)
