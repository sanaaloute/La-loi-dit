"""HTTP middleware: rate limiting, session binding and audit logging."""

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


def _identity(request: Request) -> tuple[str, int, int, Optional[str], Optional[str]]:
    """Rate-limit/session identity for one request.

    Returns ``(key, per_minute_limit, per_second_limit, user_id, jti)``. The
    Bearer JWT is decoded WITHOUT any DB hit: the tier claim drives the
    per-tier limits and the user id/sub the bucket key. Invalid/expired tokens
    degrade to the anonymous IP path — never to a 500.
    """
    settings = _settings_for(request)
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        try:
            from backend.security.jwt import decode_access_token

            payload = decode_access_token(token.strip(), settings)
            tier_cfg = catalog.get_tier(payload.tier, settings=settings)
            minute_limit = tier_cfg.get("rate_limit_per_minute") or settings.rate_limit_per_minute
            second_limit = tier_cfg.get("rate_limit_per_second") or settings.rate_limit_per_second
            return (
                f"user:{payload.user_id or payload.sub}",
                int(minute_limit),
                int(second_limit),
                payload.user_id,
                payload.jti,
            )
        except Exception:
            pass
    host = request.client.host if request.client else "unknown"
    return (
        f"ip:{host}",
        settings.rate_limit_per_minute,
        settings.rate_limit_per_second,
        None,
        None,
    )


async def _allow_shared_window(
    cache: RedisCache, identity: str, limit: int, window_seconds: int
) -> tuple[bool, int]:
    """Fixed-window counter in Redis — shared across uvicorn workers.

    Uses atomic INCR + EXPIRE. Fails OPEN when Redis hiccups (the in-process
    limiter is the safety net for dev; production runs Redis by design).
    Returns (allowed, retry_after_seconds).
    """
    bucket = int(time.time() // window_seconds)
    key = f"ratelimit:{window_seconds}s:{identity}:{bucket}"
    try:
        count = await cache._redis.incr(key)
        if count == 1:
            await cache._redis.expire(key, window_seconds + 5)
        if count > limit:
            return False, max(1, (bucket + 1) * window_seconds - int(time.time()))
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


def _session_violation_response() -> JSONResponse:
    metrics.errors_total.labels(kind="session_violation").inc()
    return JSONResponse(
        {"detail": "session invalidated by another login"},
        status_code=401,
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-tier rate limiter + single active session enforcement.

    Enforces per-second burst limits and per-minute sustained limits. With
    multiple uvicorn workers the in-process deques are per-process — effective
    limits scale with worker count; set REDIS_ENABLED so the shared Redis
    counter path is used instead.
    """

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._hits_minute: dict[str, deque[float]] = {}
        self._hits_second: dict[str, deque[float]] = {}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        key, minute_limit, second_limit, user_id, jti = _identity(request)
        settings = _settings_for(request)
        ctx = getattr(request.app.state, "ctx", None)
        cache: Optional[Any] = getattr(ctx, "cache", None)

        # --- single active session check (authenticated users only) ---
        if user_id and jti and settings.single_session_per_user:
            from backend.security.sessions import (
                device_fingerprint,
                session_scope,
                verify_active_session,
            )

            session_ok = await verify_active_session(
                user_id,
                jti,
                cache,
                fingerprint=device_fingerprint(request),
                scope=session_scope(request),
            )
            if not session_ok:
                return _session_violation_response()

        # --- per-second burst limit ---
        if isinstance(cache, RedisCache):
            allowed, retry_after = await _allow_shared_window(cache, key, second_limit, 1)
            if not allowed:
                return _rate_limit_response(retry_after)
        else:
            now = time.monotonic()
            window = self._hits_second.setdefault(key, deque())
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) >= second_limit:
                retry_after = max(1, int(1.0 - (now - window[0]))) if window else 1
                return _rate_limit_response(retry_after)
            window.append(now)

        # --- per-minute sustained limit ---
        if isinstance(cache, RedisCache):
            allowed, retry_after = await _allow_shared_window(cache, key, minute_limit, 60)
            if not allowed:
                return _rate_limit_response(retry_after)
            return await call_next(request)

        # In-process sliding window (per-process when workers > 1).
        now = time.monotonic()
        window = self._hits_minute.setdefault(key, deque())
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= minute_limit:
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
