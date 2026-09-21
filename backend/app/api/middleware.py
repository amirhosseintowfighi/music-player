"""Pure-ASGI middleware: trace id, access log, Prometheus metrics, security headers."""

from __future__ import annotations

import re
import time

import structlog
from prometheus_client import Counter, Histogram
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tmusic_common.logging import TRACE_HEADER, get_logger, new_trace_id, trace_id_var

log = get_logger("http")

REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5),
)
_TRACE_OK = re.compile(r"^[A-Za-z0-9-]{8,64}$")
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
]


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(TRACE_HEADER.lower().encode(), b"").decode()
        trace_id = incoming if _TRACE_OK.match(incoming) else new_trace_id()
        token = trace_id_var.set(trace_id)
        structlog.contextvars.clear_contextvars()
        start = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                headers.append((TRACE_HEADER.lower().encode(), trace_id.encode()))
                headers.extend(h for h in _SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed = time.perf_counter() - start
            route = scope.get("route")
            path = getattr(route, "path", "unmatched")
            if path != "/metrics":
                REQUESTS.labels(scope["method"], path, str(status)).inc()
                LATENCY.labels(scope["method"], path).observe(elapsed)
                log.info(
                    "http.request",
                    method=scope["method"],
                    route=path,
                    status=status,
                    duration_ms=round(elapsed * 1000, 1),
                )
            trace_id_var.reset(token)
