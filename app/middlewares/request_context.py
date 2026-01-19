from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core import context


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach request_id and tenant_id to context vars for logging/traceability."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        tenant_id = (
            request.headers.get("X-Org-Id")
            or request.headers.get("X-Tenant-ID")
            or context.get_tenant_id()
        )

        context.set_request_id(request_id)
        if tenant_id:
            context.set_tenant_id(tenant_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
