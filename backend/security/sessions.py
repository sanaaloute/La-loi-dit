"""Per-device active session enforcement and refresh-token storage.

Every login issues a short-lived access token with a unique ``jti`` and stores
it as the active session for that user/device. A separate long-lived refresh
token is stored under ``refresh:<user_id>:<device_id>`` so clients can request
new access tokens after the old one expires.

Native clients send ``X-Device-Id`` on every request; the web app does the same
so sessions are not bound to IP addresses. Clients without the header fall back
to a single unscoped active session for backward compatibility.
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
REFRESH_KEY_PREFIX = "refresh:"

#: Header clients send on EVERY request with a stable, self-generated device UUID.
#: Its presence switches both the session scope and the device fingerprint to
#: IP-independent variants.
DEVICE_ID_HEADER = "x-device-id"


def session_device_id(request: Request) -> Optional[str]:
    """Return the device id from the request header, or None."""
    return request.headers.get(DEVICE_ID_HEADER, "").strip() or None


def _session_key(user_id: str, device_id: Optional[str] = None) -> str:
    if device_id:
        return f"{SESSION_KEY_PREFIX}{user_id}:{device_id}"
    return f"{SESSION_KEY_PREFIX}{user_id}"


def _refresh_key(user_id: str, device_id: Optional[str] = None) -> str:
    return f"{REFRESH_KEY_PREFIX}{user_id}:{device_id or 'default'}"


def generate_jti() -> str:
    """Return a cryptographically random JWT ID."""
    return secrets.token_urlsafe(32)


def device_fingerprint(request: Request) -> str:
    """Stable but coarse device identifier from request metadata.

    Intentionally not bullet-proof — the goal is to prevent trivial session
    sharing across browsers, not to fingerprint hardware. When a client sends
    ``X-Device-Id`` the fingerprint is derived from that value only, so roaming
    IPs, VPNs and mobile-network handovers never log the user out. Clients
    without the header fall back to browser metadata plus the client IP.
    """
    device_id = session_device_id(request)
    if device_id:
        return hashlib.sha256(f"device:{device_id}".encode("utf-8")).hexdigest()[:32]
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
    *,
    device_id: Optional[str] = None,
) -> None:
    """Store ``jti`` as the active access session for ``user_id`` (+device).

    ``exp`` is the Unix timestamp at which the token expires; the stored
    session is TTL'd to that same horizon. If ``cache`` is unavailable the
    call is a no-op (session enforcement degrades gracefully).
    """
    if cache is None or not user_id or not jti:
        return
    ttl = max(60, int(exp - time.time()))
    await cache.set(
        _session_key(user_id, device_id),
        {"jti": jti, "fingerprint": fingerprint, "exp": exp},
        ttl=ttl,
    )
    logger.debug(
        "session activated",
        extra={"user_id": user_id, "jti": jti[:8], "device_id": device_id},
    )


async def verify_active_session(
    user_id: str,
    jti: Optional[str],
    cache: Optional[CacheProtocol],
    *,
    fingerprint: Optional[str] = None,
    device_id: Optional[str] = None,
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
        session: Optional[dict[str, Any]] = await cache.get(_session_key(user_id, device_id))
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


async def revoke_session(
    user_id: str,
    cache: Optional[CacheProtocol],
    *,
    device_id: Optional[str] = None,
) -> None:
    """Hard-revoke the stored active session for this user/device (logout).

    The entry is POISONED, not deleted: ``verify_active_session`` fails open
    when no entry exists (so a cache flush never mass-locks users out), which
    means a plain delete would leave the token fully valid. Revocation must
    therefore leave behind an entry whose ``jti`` can never match, keeping
    the original expiry horizon so the poison expires with the token.
    """
    if cache is None or not user_id:
        return
    key = _session_key(user_id, device_id)
    try:
        session: Optional[dict[str, Any]] = await cache.get(key)
    except Exception:
        session = None
    now = int(time.time())
    exp = int(session.get("exp") or 0) if isinstance(session, dict) else 0
    ttl = max(60, exp - now) if exp else 3600  # unknown horizon: poison briefly
    await cache.set(
        key,
        {
            "jti": f"revoked:{secrets.token_urlsafe(16)}",
            "fingerprint": None,
            "exp": exp or (now + ttl),
        },
        ttl=ttl,
    )


async def store_refresh_token(
    user_id: str,
    jti: str,
    device_id: Optional[str],
    exp: int,
    cache: Optional[CacheProtocol],
) -> None:
    """Persist a refresh token so it can be verified later."""
    if cache is None or not user_id or not jti:
        return
    ttl = max(60, int(exp - time.time()))
    await cache.set(
        _refresh_key(user_id, device_id),
        {"jti": jti, "device_id": device_id, "exp": exp},
        ttl=ttl,
    )


async def verify_refresh_token(
    user_id: str,
    jti: str,
    device_id: Optional[str],
    cache: Optional[CacheProtocol],
) -> bool:
    """Return True if a matching refresh token is stored for this user/device."""
    if cache is None or not user_id or not jti:
        return False
    try:
        stored: Optional[dict[str, Any]] = await cache.get(_refresh_key(user_id, device_id))
    except Exception:
        return False
    if not isinstance(stored, dict):
        return False
    if stored.get("jti") != jti:
        return False
    if stored.get("exp", 0) < int(time.time()):
        return False
    return True


async def revoke_refresh_tokens(user_id: str, cache: Optional[CacheProtocol]) -> None:
    """Revoke every stored refresh token for ``user_id`` (logout all devices)."""
    if cache is None or not user_id:
        return
    await cache.clear_prefix(f"{REFRESH_KEY_PREFIX}{user_id}:")


async def revoke_all_sessions(user_id: str, cache: Optional[CacheProtocol]) -> None:
    """Remove the stored active session and refresh tokens in every scope
    (account deletion, password change) so all devices are forced to log in again."""
    if cache is None or not user_id:
        return
    # The unscoped key is used by clients that do not send X-Device-Id.
    await revoke_session(user_id, cache)
    await cache.clear_prefix(f"{SESSION_KEY_PREFIX}{user_id}:")
    await revoke_refresh_tokens(user_id, cache)
