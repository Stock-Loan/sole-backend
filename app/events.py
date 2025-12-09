import logging

from fastapi import FastAPI

from app.db.init_db import init_db

logger = logging.getLogger(__name__)


def register_event_handlers(app: FastAPI) -> None:
    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info("Application startup")
        await init_db()

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        logger.info("Application shutdown")
