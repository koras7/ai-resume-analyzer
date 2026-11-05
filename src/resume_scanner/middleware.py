"""Request context middleware: request IDs, one access log line and HTTP metrics per request.

Written as plain ASGI middleware (not BaseHTTPMiddleware) so the ContextVar it
sets is visible to the endpoint and everything it calls.
"""

import logging
import re
import time
import uuid

from resume_scanner import metrics
from resume_scanner.logging_config import request_id_var

logger = logging.getLogger("resume_scanner.access")

REQUEST_ID_HEADER = "x-request-id"
# Only trust short, simple IDs from clients; anything else could inject junk into logs.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Probes and scrapers hit these every few seconds; log them at DEBUG to keep logs readable.
_QUIET_PATHS = {"/health", "/ready", "/metrics"}
# Scrapes of /metrics are left out of the HTTP metrics so they don't skew request rate.
_UNMEASURED_PATHS = {"/metrics"}


def _incoming_request_id(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None


class RequestContextMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500  # stays 500 if the app raises before sending a response
        measured = scope["path"] not in _UNMEASURED_PATHS
        if measured:
            metrics.HTTP_REQUESTS_IN_PROGRESS.inc()

        async def send_with_request_id(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception(
                "unhandled exception",
                extra={"method": scope["method"], "path": scope["path"]},
            )
            raise
        finally:
            duration_s = time.perf_counter() - started
            if measured:
                metrics.HTTP_REQUESTS_IN_PROGRESS.dec()
                # The router has filled in scope["route"] by now, so we label by template.
                metrics.observe_request(
                    scope["method"], metrics.route_label(scope), status_code, duration_s
                )
            path = scope["path"]
            level = logging.DEBUG if path in _QUIET_PATHS else logging.INFO
            logger.log(
                level,
                "request completed",
                extra={
                    "method": scope["method"],
                    "path": path,
                    "status": status_code,
                    "duration_ms": round(duration_s * 1000, 2),
                },
            )
            request_id_var.reset(token)
