"""Bound upload bodies before Starlette's multipart parser spools them.

Preflight and replay are necessary because FastAPI translates receive errors
raised during multipart parsing into HTTP 400, hiding a streaming size error.
The preflight spool is bounded, and is closed as soon as the request finishes.
"""

import json
from tempfile import SpooledTemporaryFile
from typing import Any, Awaitable, Callable

from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from app import request_context


MULTIPART_OVERHEAD_BYTES = 64 * 1024


class RequestBodyLimitMiddleware:
    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        *,
        max_body_bytes: int,
        path: str = "/api/assets",
        method: str = "POST",
        preflight: Callable[[dict], None] | None = None,
    ) -> None:
        if max_body_bytes < 0:
            raise ValueError("max_body_bytes must be non-negative")
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.path = path
        self.method = method
        self.preflight = preflight

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != self.method
            or scope.get("path") != self.path
        ):
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", ()):
            if name.lower() == b"content-length":
                try:
                    declared_size = int(value)
                except ValueError:
                    continue  # The stream limit still protects malformed headers.
                if declared_size > self.max_body_bytes:
                    await self._reject(send)
                    return

        if self.preflight is not None:
            try:
                await run_in_threadpool(self.preflight, scope)
            except HTTPException as exc:
                # The same body as every other API error (app/main.py): detail, a stable code, the request ID.
                code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(exc.status_code, "error")
                response = JSONResponse({"detail": exc.detail, "code": code,
                                         "request_id": request_context.request_id.get()},
                                        status_code=exc.status_code, headers=exc.headers)
                await response(scope, receive, send)
                return

        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            received_bytes = 0
            disconnected = False
            while True:
                event = await receive()
                if event["type"] == "http.disconnect":
                    disconnected = True
                    break
                if event["type"] != "http.request":
                    raise RuntimeError(f"Unexpected ASGI receive event: {event['type']}")
                chunk = event.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > self.max_body_bytes:
                    await self._reject(send)
                    return
                spool.write(chunk)
                if not event.get("more_body", False):
                    break

            spool.seek(0)
            remaining = received_bytes
            terminal_sent = False

            async def replay_receive() -> dict:
                nonlocal remaining, terminal_sent
                if remaining:
                    chunk = spool.read(min(1024 * 1024, remaining))
                    remaining -= len(chunk)
                    if not remaining and not disconnected:
                        terminal_sent = True
                    return {
                        "type": "http.request",
                        "body": chunk,
                        "more_body": bool(remaining) or disconnected,
                    }
                if disconnected:
                    return {"type": "http.disconnect"}
                if not terminal_sent:
                    terminal_sent = True
                    return {"type": "http.request", "body": b"", "more_body": False}
                return await receive()

            await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send: Callable) -> None:
        body = json.dumps({"detail": "File exceeds 100 MB", "code": "payload_too_large",
                           "request_id": request_context.request_id.get()}).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
