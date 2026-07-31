"""HTTP middleware: sliding-window rate limiting and audit logging."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.core import catalog
from backend.core.cache import RedisCache
from backend.core.config import get_settings
from backend.observability import metrics

logger = logging.getLogger(__name__)

# Probing endpoints stay unthrottled so orchestrators/monitors never get 429s.
_EXEMPT_PATHS = {"/health", "/ready", "/metrics"}


def _settings_for(request: Request):
    ctx = getattr(request.app.state, "ctx", None)
    return ctx.settings if ctx is not None else get_settings()


def _identity(request: Request) -> tuple[str, int]:
    """Rate-limit (key, per-minute limit) for one request.

    The Bearer JWT is decoded WITHOUT any DB hit: the tier claim drives the
    per-tier limit and the user id/sub the bucket key. Invalid/expired tokens
    degrade to the anonymous IP path — never to a 500.
    """
    settings = _settings_for(request)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            from backend.security.jwt import decode_access_token

            payload = decode_access_token(token.strip(), settings)
            limit = catalog.get_tier(payload.tier, settings=settings).get(
                "rate_limit_per_minute"
            ) or settings.rate_limit_per_minute
            return f"user:{payload.user_id or payload.sub}", int(limit)
        except Exception:
            pass
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}", settings.rate_limit_per_minute


async def _allow_shared(cache: RedisCache, identity: str, limit: int) -> tuple[bool, int]:
    """Fixed-window counter in Redis — shared across uvicorn workers.

    Uses atomic INCR + EXPIRE. Fails OPEN when Redis hiccups (the in-process
    limiter is the safety net for dev; production runs Redis by design).
    Returns (allowed, retry_after_seconds).
    """
    bucket = int(time.time() // 60)
    key = f"ratelimit:{identity}:{bucket}"
    try:
        count = await cache._redis.incr(key)
        if count == 1:
            await cache._redis.expire(key, 130)
        if count > limit:
            return False, max(1, (bucket + 1) * 60 - int(time.time()))
        return True, 0
    except Exception:
        return True, 0


def _rate_limit_response(retry_after: int) -> JSONResponse:
    metrics.errors_total.labels(kind="rate_limit").inc()
    return JSONResponse(
        {"detail": "rate limit exceeded"},
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-tier rate limiter (sliding window in-process, fixed window in Redis).

    With multiple uvicorn workers the in-process deques are per-process —
    effective limits scale with the worker count; set REDIS_ENABLED so the
    shared Redis counter path is used instead.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._hits: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        key, limit = _identity(request)

        # Shared path: Redis-backed fixed window (multi-worker safe).
        ctx = getattr(request.app.state, "ctx", None)
        cache: Optional[Any] = getattr(ctx, "cache", None)
        if isinstance(cache, RedisCache):
            allowed, retry_after = await _allow_shared(cache, key, limit)
            if not allowed:
                return _rate_limit_response(retry_after)
            return await call_next(request)

        # In-process sliding window (per-process when workers > 1).
        now = time.monotonic()
        window = self._hits.setdefault(key, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= limit:
            retry_after = max(1, int(60.0 - (now - window[0]))) if window else 60
            return _rate_limit_response(retry_after)
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
