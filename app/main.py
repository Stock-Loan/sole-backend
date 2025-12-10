from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api.v1 import api_router
from app.core.limiter import limiter
from app.core.logging import configure_logging
from app.core.settings import settings
from app.events import register_event_handlers
from app.middlewares.request_context import RequestContextMiddleware
from app.middlewares.security_headers import SecurityHeadersMiddleware


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="SOLE Backend", version="0.1.0")
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, limiter._rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.enable_hsts)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router, prefix="/api/v1")
    register_event_handlers(app)
    return app


app = create_app()
