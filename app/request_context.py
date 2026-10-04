"""Per-request context (Phase 24): the request ID and the resolved client address.

``app/http_security.py`` sets both for every HTTP request; logs (``app/logs.py``) and audit
events (``app/audit.py``) read them, so a log line, an audit row and the ``X-Request-ID``
response header can be matched. Outside a request (workers, CLI) both are ``None``.
"""
from contextvars import ContextVar

request_id: ContextVar[str | None] = ContextVar("reelforge_request_id", default=None)
client_ip: ContextVar[str | None] = ContextVar("reelforge_client_ip", default=None)
