from starlette.types import ASGIApp, Receive, Scope, Send, Message

from app.core.settings import settings


class SecurityHeadersMiddleware:
    """Apply a set of safe default security headers for HTTP responses."""

    def __init__(self, app: ASGIApp, enable_hsts: bool = True) -> None:
        self.app = app
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                defaults: list[tuple[bytes, bytes]] = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-xss-protection", b"0"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                ]
                if self.enable_hsts:
                    defaults.append((
                        b"strict-transport-security",
                        b"max-age=63072000; includeSubDomains; preload",
                    ))
                if settings.content_security_policy:
                    header_name = (
                        b"content-security-policy-report-only"
                        if settings.content_security_policy_report_only
                        else b"content-security-policy"
                    )
                    defaults.append((header_name, settings.content_security_policy.encode()))

                existing_keys = {k for k, _ in headers}
                new_headers = list(message.get("headers", []))
                for key, value in defaults:
                    if key not in existing_keys:
                        new_headers.append((key, value))
                message["headers"] = new_headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
