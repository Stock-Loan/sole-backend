from __future__ import annotations

import asyncio
import json

from starlette.types import ASGIApp, Receive, Scope, Send


class ConcurrencyLimitMiddleware:
    def __init__(self, app: ASGIApp, limit: int, timeout_seconds: float | None = None) -> None:
        self.app = app
        self._limit = limit
        self._timeout = timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        self._semaphore = asyncio.Semaphore(limit) if limit > 0 else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._semaphore:
            await self.app(scope, receive, send)
            return

        acquired = False
        try:
            if self._timeout is None:
                await self._semaphore.acquire()
            else:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=self._timeout)
            acquired = True
            await self.app(scope, receive, send)
        except asyncio.TimeoutError:
            retry_after = str(int(self._timeout)) if self._timeout else "1"
            body = json.dumps({
                "code": "server_busy",
                "message": "Server is handling too many requests",
                "data": None,
                "details": {"detail": "Concurrency limit reached. Please retry."},
            }).encode()
            await send({
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", retry_after.encode()),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": body,
            })
        finally:
            if acquired:
                self._semaphore.release()
