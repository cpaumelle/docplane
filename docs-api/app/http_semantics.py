"""HTTP semantics shared by every DocPlane route."""
from __future__ import annotations

from typing import Any, Awaitable, Callable


class HeadAsGetMiddleware:
    """Execute HEAD through the corresponding GET route and suppress the body.

    Starlette/FastAPI does not automatically install HEAD handlers for API GET
    routes. This preserves the GET status and headers (including authentication
    and discovery headers) while returning no response body.
    """

    def __init__(self, app: Callable[..., Awaitable[Any]]):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "HEAD":
            await self.app(scope, receive, send)
            return

        get_scope = dict(scope)
        get_scope["method"] = "GET"

        async def send_head(message):
            if message.get("type") == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app(get_scope, receive, send_head)
