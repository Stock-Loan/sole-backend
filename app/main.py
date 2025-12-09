from fastapi import FastAPI

from app.api.v1 import api_router
from app.core.logging import configure_logging
from app.events import register_event_handlers


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="SOLE Backend", version="0.1.0")
    app.include_router(api_router, prefix="/api/v1")
    register_event_handlers(app)
    return app


app = create_app()
