"""HTTP middleware: sliding-window rate limiting and audit logging."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.core.config import get_settings
from backend.observability import metrics

logger = logging.getLogger(__name__)

# Probing endpoints stay unthrottled so orchestrators/monitors never get 429s.
_EXEMPT_PATHS = {"/health", "/ready", "/metrics"}


def _settings_for(request: Request):
    ctx = getattr(request.app.state, "ctx", None)
    return ctx.settings if ctx is not None else get_settings()


def _client_key(request: Request) -> str:
    """Rate-limit identity: token subject when decodable, else client IP."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            from backend.security.jwt import decode_access_token

            return f"user:{decode_access_token(token.strip(), _settings_for(request)).sub}"
        except Exception:
            pass
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-memory sliding-window rate limiter (per user or per IP)."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        limit = _settings_for(request).rate_limit_per_minute
        key = _client_key(request)
        now = time.monotonic()
        window = self._hits.setdefault(key, deque())

        while window and now - window[0] >= 60.0:
            window.popleft()

        if len(window) >= limit:
            retry_after = max(1, int(60.0 - (now - window[0]))) if window else 60
            metrics.errors_total.labels(kind="rate_limit").inc()
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )

        window.append(now)
        return await call_next(request)


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Records every request into a ring buffer on app.state and the logger.

    Also feeds the HTTP Prometheus counters/histograms.
    """

    async def dispatch(self, request: Request, call_next):
        started = time.perf_counter()
        status = 500
        cap = _settings_for(request).audit_log_cap
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            user = "anonymous"
            header = request.headers.get("authorization", "")
            scheme, _, token = header.partition(" ")
            if scheme.lower() == "bearer" and token:
                try:
                    from backend.security.jwt import decode_access_token

                    user = decode_access_token(token.strip(), _settings_for(request)).sub
                except Exception:
                    user = "invalid-token"

            entry = {
                "ts": time.time(),
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "latency_ms": round(latency_ms, 1),
                "user": user,
            }

            app = request.app
            buf = getattr(app.state, "audit_log", None)
            if buf is None:
                buf = deque(maxlen=cap)
                app.state.audit_log = buf
            buf.append(entry)

            logger.info(
                "http request",
                extra={
                    "method": entry["method"],
                    "path": entry["path"],
                    "status": status,
                    "latency_ms": entry["latency_ms"],
                    "user": user,
                },
            )
            metrics.http_requests_total.labels(
                method=request.method, path=request.url.path, status=str(status)
            ).inc()
            metrics.http_request_latency_seconds.observe(latency_ms / 1000.0)
            if status >= 500:
                metrics.errors_total.labels(kind="http_5xx").inc()
