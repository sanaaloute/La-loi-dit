"""Single active session enforcement per user/device.

Every login issues a JWT with a unique ``jti`` and stores the active session
in Redis (or the in-memory fallback) keyed by ``user_id``. Subsequent requests
must carry a token whose ``jti`` matches the stored value; a new login from a
different device automatically invalidates the previous session.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any, Optional

from fastapi import Request

from backend.core.cache import CacheProtocol

logger = logging.getLogger(__name__)

SESSION_KEY_PREFIX = "active_session:"


def _session_key(user_id: str) -> str:
    return f"{SESSION_KEY_PREFIX}{user_id}"


def generate_jti() -> str:
    """Return a cryptographically random JWT ID."""
    return secrets.token_urlsafe(32)


def device_fingerprint(request: Request) -> str:
    """Stable but coarse device identifier from request metadata.

    Intentionally not bullet-proof — the goal is to prevent trivial session
    sharing across browsers, not to fingerprint hardware. IP alone is
    avoided as the sole signal because of NAT/CGNAT.
    """
    user_agent = request.headers.get("user-agent", "")
    accept_lang = request.headers.get("accept-language", "")
    host = request.client.host if request.client else "unknown"
    raw = f"{user_agent}|{accept_lang}|{host}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


async def activate_session(
    user_id: str,
    jti: str,
    fingerprint: str,
    exp: int,
    cache: Optional[CacheProtocol],
) -> None:
    """Store ``jti`` as the single active session for ``user_id``.

    ``exp`` is the Unix timestamp at which the token expires; the stored
    session is TTL'd to that same horizon. If ``cache`` is unavailable the
    call is a no-op (session enforcement degrades gracefully).
    """
    if cache is None or not user_id or not jti:
        return
    ttl = max(60, int(exp - time.time()))
    await cache.set(
        _session_key(user_id),
        {"jti": jti, "fingerprint": fingerprint, "exp": exp},
        ttl=ttl,
    )
    logger.debug("session activated", extra={"user_id": user_id, "jti": jti[:8]})


async def verify_active_session(
    user_id: str,
    jti: Optional[str],
    cache: Optional[CacheProtocol],
    *,
    fingerprint: Optional[str] = None,
) -> bool:
    """Return True when ``jti`` matches the active session for ``user_id``.

    Missing cache, missing user_id, or missing token enforcement settings are
    treated as allowed (fail-open) so auth never hard-fails because of a
    transient Redis outage. Callers should still reject the request when this
    returns False.
    """
    if cache is None or not user_id or not jti:
        return True
    try:
        session: Optional[dict[str, Any]] = await cache.get(_session_key(user_id))
    except Exception:
        return True
    if session is None:
        # No active session recorded: either Redis was cleared or the token
        # predates session enforcement. Treat as valid to avoid mass lockout.
        return True
    if session.get("jti") != jti:
        return False
    stored_fingerprint = session.get("fingerprint")
    if fingerprint is not None and stored_fingerprint is not None and stored_fingerprint != fingerprint:
        return False
    return True


async def revoke_session(user_id: str, cache: Optional[CacheProtocol]) -> None:
    """Remove the stored active session (logout / global revoke)."""
    if cache is None or not user_id:
        return
    await cache.delete(_session_key(user_id))
