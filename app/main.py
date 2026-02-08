from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1 import api_router
from app.core.errors import register_exception_handlers
from app.core.response_envelope import register_response_envelope
from app.core.limiter import limiter
from app.core.logging import configure_logging
from app.core.settings import settings
from app.events import register_event_handlers
from app.middlewares.concurrency_limit import ConcurrencyLimitMiddleware
from app.middlewares.request_context import RequestContextMiddleware
from app.middlewares.security_headers import SecurityHeadersMiddleware
from app.middlewares.trust_proxies import TrustedProxiesMiddleware


def create_app() -> FastAPI:
    configure_logging()
    is_dev = settings.environment.lower() in {"development", "dev"}
    app = FastAPI(
        title="SOLE Backend",
        version="0.1.0",
        docs_url="/docs" if is_dev else None,
        redoc_url="/redoc" if is_dev else None,
        openapi_url="/openapi.json" if is_dev else None,
    )
    register_exception_handlers(app)
    register_response_envelope(app)
    app.state.limiter = limiter
    app.add_middleware(SlowAPIMiddleware)
    if settings.request_concurrency_limit > 0:
        app.add_middleware(
            ConcurrencyLimitMiddleware,
            limit=settings.request_concurrency_limit,
            timeout_seconds=settings.request_concurrency_timeout_seconds,
        )
    app.add_middleware(TrustedProxiesMiddleware, proxies_count=settings.proxies_count)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, enable_hsts=settings.enable_hsts)
    origins = settings.allowed_origins_list()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Org-Id", "X-Tenant-ID", "X-CSRF-Token", "X-Step-Up-Token", "X-Background-Request"],
    )
    app.include_router(api_router, prefix="/api/v1")
    register_event_handlers(app)
    return app


app = create_app()
