"""ASGI middleware.

The body-size cap has to live here, not in the route handler. Starlette parses
the *entire* multipart body before the endpoint function is entered, so by the
time handler code could inspect the upload, a 201 MB body has already been
spooled to disk. Counting bytes as they arrive off the wire is the only place
the limit can actually be enforced.
"""

import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger

logger = get_logger(__name__)

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class _BodyTooLarge(Exception):
    def __init__(self, received: int, limit: int) -> None:
        super().__init__(f"Body exceeded {limit} bytes")
        self.received = received
        self.limit = limit


class MaxBodySizeMiddleware:
    """Rejects oversized request bodies before they can be buffered."""

    def __init__(self, app: ASGIApp, *, max_bytes: int, prefixes: tuple[str, ...] = ("/api/uploads",)):
        self.app = app
        self.max_bytes = max_bytes
        self.prefixes = prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(self.prefixes):
            await self.app(scope, receive, send)
            return

        # Cheap early rejection. Not authoritative: nginx runs with
        # `proxy_request_buffering off`, which uses chunked encoding and omits
        # Content-Length entirely.
        content_length = Headers(scope=scope).get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
            await self._reject(send, int(content_length))
            return

        received_total = 0

        async def counting_receive() -> Message:
            nonlocal received_total
            message = await receive()
            if message["type"] == "http.request":
                received_total += len(message.get("body", b""))
                if received_total > self.max_bytes:
                    raise _BodyTooLarge(received_total, self.max_bytes)
            return message

        try:
            await self.app(scope, counting_receive, send)
        except _BodyTooLarge as exc:
            # Safe to send a response here: the exception is raised while the
            # body is still being read, so nothing has been sent downstream.
            await self._reject(send, exc.received)

    async def _reject(self, send: Send, received: int) -> None:
        import json

        body = json.dumps(
            {
                "error": {
                    "code": "FILE_TOO_LARGE",
                    "message": (
                        f"Upload exceeds the maximum size of {self.max_bytes} bytes "
                        f"({self.max_bytes / 1_048_576:.0f} MB)."
                    ),
                    "details": {"limit_bytes": self.max_bytes, "received_bytes": received},
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class RequestIdMiddleware:
    """Assigns each request an id and echoes it back as ``X-Request-Id``."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id")
        request_id = incoming or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_ctx.reset(token)
