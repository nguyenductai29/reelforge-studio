"""HTTP hardening for the API (Phase 24), as one pure ASGI middleware (streaming responses pass through).

For every request:

* **Request ID.** ``X-Request-ID`` from a trusted proxy (``app/client_ip.py``) when it is
  well formed, otherwise a new one. It is in ``app.request_context`` for logs and audit
  events, and in the response header (error bodies repeat it as ``request_id``).
* **Client address.** Resolved once (``CF-Connecting-IP`` behind a trusted proxy only).
* **Cross-site request protection.** A state-changing request (POST, PUT, PATCH, DELETE)
  to ``/api/`` must come from the frontend origin (or the API's own): its ``Origin``,
  or failing that its ``Referer``, has to match. A request that carries the session
  cookie must have one of the two at all: a browser always sends them, so a missing
  one means a request that did not come from our pages. Requests without the session
  cookie (scripts such as ``npm run create-admin``) may omit both. Provider webhooks
  (``/api/webhooks/``) are exempt: they are authenticated by their signatures.
  The session cookie is also ``SameSite=Strict``, so other sites cannot send it at all.
* **Security headers** on every response: ``X-Content-Type-Options: nosniff``,
  ``Referrer-Policy``, ``X-Frame-Options: DENY``, ``Permissions-Policy``,
  ``Cross-Origin-Opener-Policy``, a locked-down ``Content-Security-Policy`` for API
  responses (JSON and media never run scripts), and ``Strict-Transport-Security`` when
  the public origin is HTTPS. The Next.js pages get their own policy (next.config.ts).
* **Metrics.** Method, route template (never an ID or a query), status and duration
  (``app/metrics.py``).
"""
import json
import re
import time
from typing import Callable
from urllib.parse import urlsplit
import uuid

from starlette.datastructures import MutableHeaders

from app import client_ip, metrics, request_context

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
EXEMPT_PREFIXES = ("/api/webhooks/",)
SESSION_COOKIE = "rf_session"
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{8,64}")
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox"
PERMISSIONS_POLICY = ("accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), "
                      "payment=(), usb=(), interest-cohort=()")
HSTS = "max-age=31536000"


def _header(scope, name: bytes) -> str | None:
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def _origin_of(url: str | None) -> str | None:
    if not url:
        return None
    if url == "null":
        return "null"
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def request_origin(scope) -> str | None:
    """The origin a browser request came from: ``Origin``, else the origin of ``Referer``."""
    origin = _header(scope, b"origin")
    if origin is not None:
        return _origin_of(origin.strip())
    return _origin_of(_header(scope, b"referer"))


def own_origin(scope) -> str:
    host = _header(scope, b"host") or ""
    return f"{scope.get('scheme', 'http')}://{host}".lower()


def origin_ok(scope, allowed: set[str], *, require: bool) -> bool:
    """Whether a state-changing request may proceed (see the module docstring)."""
    origin = request_origin(scope)
    if origin is None:
        return not require
    return origin.rstrip("/") in {own_origin(scope), *(item.rstrip("/").lower() for item in allowed if item)}


def has_session_cookie(scope) -> bool:
    cookie = _header(scope, b"cookie") or ""
    return any(part.strip().startswith(f"{SESSION_COOKIE}=") for part in cookie.split(";"))


def route_template(scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class SecurityMiddleware:
    def __init__(self, app, *, allowed_origins: Callable[[], set[str]], https: Callable[[], bool]):
        self.app = app
        self.allowed_origins = allowed_origins
        self.https = https

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        address = client_ip.resolve_scope(scope)
        incoming = _header(scope, b"x-request-id")
        peer = (scope.get("client") or (None,))[0]
        rid = incoming if incoming and REQUEST_ID_PATTERN.fullmatch(incoming) and client_ip.is_trusted(peer) \
            else uuid.uuid4().hex
        rid_token = request_context.request_id.set(rid)
        ip_token = request_context.client_ip.set(address)
        # Also on the request state: Starlette's outermost error handler runs after this middleware returned.
        scope.setdefault("state", {})["request_id"] = rid
        method, path = scope.get("method", "GET"), scope.get("path", "")
        started = time.perf_counter()
        status = {"code": 500}
        hsts = False
        try:
            try:
                hsts = self.https()
            except Exception:  # noqa: BLE001 - headers never fail a request
                hsts = False

            async def send_wrapper(message):
                if message["type"] == "http.response.start":
                    status["code"] = message["status"]
                    headers = MutableHeaders(scope=message)
                    headers["X-Request-ID"] = rid
                    for name, value in (("X-Content-Type-Options", "nosniff"),
                                        ("Referrer-Policy", "strict-origin-when-cross-origin"),
                                        ("X-Frame-Options", "DENY"), ("Permissions-Policy", PERMISSIONS_POLICY),
                                        ("Cross-Origin-Opener-Policy", "same-origin")):
                        if name not in headers:
                            headers[name] = value
                    if path.startswith("/api/") and "Content-Security-Policy" not in headers:
                        headers["Content-Security-Policy"] = API_CSP
                    if hsts and "Strict-Transport-Security" not in headers:
                        headers["Strict-Transport-Security"] = HSTS
                await send(message)

            if method in UNSAFE_METHODS and path.startswith("/api/") and not path.startswith(EXEMPT_PREFIXES):
                try:
                    allowed = self.allowed_origins()
                except Exception:  # noqa: BLE001 - without the setting, only the API's own origin is accepted
                    allowed = set()
                if not origin_ok(scope, allowed, require=has_session_cookie(scope)):
                    body = json.dumps({"detail": "Invalid origin", "code": "invalid_origin",
                                       "request_id": rid}).encode("utf-8")
                    await send_wrapper({"type": "http.response.start", "status": 403,
                                        "headers": [(b"content-type", b"application/json"),
                                                    (b"content-length", str(len(body)).encode("ascii"))]})
                    await send_wrapper({"type": "http.response.body", "body": body})
                    return
            await self.app(scope, receive, send_wrapper)
        finally:
            metrics.observe_http(method, route_template(scope), status["code"], time.perf_counter() - started)
            request_context.request_id.reset(rid_token)
            request_context.client_ip.reset(ip_token)
