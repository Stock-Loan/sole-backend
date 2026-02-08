from uuid import uuid4

from starlette.types import ASGIApp, Receive, Scope, Send, Message

from app.core import context


class RequestContextMiddleware:
    """Attach request_id and tenant_id to context vars for logging/traceability."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        request_id = (
            headers.get(b"x-request-id", b"").decode() or str(uuid4())
        )
        tenant_id = (
            headers.get(b"x-org-id", b"").decode()
            or headers.get(b"x-tenant-id", b"").decode()
            or context.get_tenant_id()
        )

        context.set_request_id(request_id)
        if tenant_id:
            context.set_tenant_id(tenant_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers_list = list(message.get("headers", []))
                headers_list.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers_list
            await send(message)

        await self.app(scope, receive, send_with_request_id)
