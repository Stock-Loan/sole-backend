from __future__ import annotations

import asyncio

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class ConcurrencyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limit: int, timeout_seconds: float | None = None) -> None:
        super().__init__(app)
        self._limit = limit
        self._timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self._semaphore = asyncio.Semaphore(limit) if limit > 0 else None

    async def dispatch(self, request, call_next):  # type: ignore[override]
        if not self._semaphore:
            return await call_next(request)

        acquired = False
        try:
            if self._timeout is None:
                await self._semaphore.acquire()
            else:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self._timeout)
            acquired = True
            return await call_next(request)
        except asyncio.TimeoutError:
            retry_after = str(int(self._timeout)) if self._timeout else "1"
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": retry_after},
                content={
                    "code": "server_busy",
                    "message": "Server is handling too many requests",
                    "data": None,
                    "details": {"detail": "Concurrency limit reached. Please retry."},
                },
            )
        finally:
            if acquired:
                self._semaphore.release()
