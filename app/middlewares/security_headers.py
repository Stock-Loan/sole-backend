from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.settings import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Apply a set of safe default security headers for HTTP responses."""

    def __init__(self, app, enable_hsts: bool = True) -> None:  # type: ignore[override]
        super().__init__(app)
        self.enable_hsts = enable_hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-XSS-Protection", "0")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if self.enable_hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"
            )
        if settings.content_security_policy:
            header = (
                "Content-Security-Policy-Report-Only"
                if settings.content_security_policy_report_only
                else "Content-Security-Policy"
            )
            response.headers.setdefault(header, settings.content_security_policy)
        return response
