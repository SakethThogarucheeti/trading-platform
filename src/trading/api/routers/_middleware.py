from __future__ import annotations

import logging
import time
import uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            scope["state"] = getattr(scope, "state", {})

            async def send_with_header(message: dict) -> None:  # type: ignore[type-arg]
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode()))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_header)
        else:
            await self.app(scope, receive, send)


class AccessLogMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        start = time.monotonic()
        status_code = 0

        async def send_with_logging(message: dict) -> None:  # type: ignore[type-arg]
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        await self.app(scope, receive, send_with_logging)
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug(
            "%s %s → %d (%.0fms)", request.method, request.url.path, status_code, elapsed_ms
        )
