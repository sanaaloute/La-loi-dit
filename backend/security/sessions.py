"""Single active session enforcement per user/device class.

Every login issues a JWT with a unique ``jti`` and stores the active session
in Redis (or the in-memory fallback) keyed by ``user_id`` plus a device-class
scope: web sessions use the historical unscoped key, native clients sending
``X-Device-Id`` land in the ``mobile`` scope. Subsequent requests must carry
a token whose ``jti`` matches the stored value; a new login from the same
device class automatically invalidates the previous session of that class,
while leaving the other class (phone vs. browser) untouched.
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

#: Header native clients (mobile apps) send on EVERY request with a stable,
#: self-generated device UUID. Its presence switches both the session scope
#: and the device fingerprint to IP-independent variants.
DEVICE_ID_HEADER = "x-device-id"

MOBILE_SCOPE = "mobile"


def session_scope(request: Request) -> str:
    """Return the session scope for this request ("mobile" or "" for web).

    Web sessions keep the historical unscoped key so sessions issued before
    scoping existed stay valid after a deploy.
    """
    return MOBILE_SCOPE if request.headers.get(DEVICE_ID_HEADER) else ""


def _session_key(user_id: str, scope: str = "") -> str:
    if scope:
        return f"{SESSION_KEY_PREFIX}{user_id}:{scope}"
    return f"{SESSION_KEY_PREFIX}{user_id}"


def generate_jti() -> str:
    """Return a cryptographically random JWT ID."""
    return secrets.token_urlsafe(32)


def device_fingerprint(request: Request) -> str:
    """Stable but coarse device identifier from request metadata.

    Intentionally not bullet-proof — the goal is to prevent trivial session
    sharing across browsers, not to fingerprint hardware. IP alone is
    avoided as the sole signal because of NAT/CGNAT.

    Native clients (``X-Device-Id`` header) are fingerprinted on their
    self-supplied stable device UUID only: mobile networks change IPs
    mid-session (CGNAT, Wi-Fi <-> cellular handover), so binding their
    fingerprint to request metadata would log them out at random.
    """
    device_id = request.headers.get(DEVICE_ID_HEADER, "").strip()
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
    scope: str = "",
) -> None:
    """Store ``jti`` as the single active session for ``user_id`` (+scope).

    ``exp`` is the Unix timestamp at which the token expires; the stored
    session is TTL'd to that same horizon. If ``cache`` is unavailable the
    call is a no-op (session enforcement degrades gracefully).
    """
    if cache is None or not user_id or not jti:
        return
    ttl = max(60, int(exp - time.time()))
    await cache.set(
        _session_key(user_id, scope),
        {"jti": jti, "fingerprint": fingerprint, "exp": exp},
        ttl=ttl,
    )
    logger.debug("session activated", extra={"user_id": user_id, "jti": jti[:8], "scope": scope})


async def verify_active_session(
    user_id: str,
    jti: Optional[str],
    cache: Optional[CacheProtocol],
    *,
    fingerprint: Optional[str] = None,
    scope: str = "",
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
        session: Optional[dict[str, Any]] = await cache.get(_session_key(user_id, scope))
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


async def revoke_session(user_id: str, cache: Optional[CacheProtocol], *, scope: str = "") -> None:
    """Hard-revoke the stored active session (logout / account deletion).

    The entry is POISONED, not deleted: ``verify_active_session`` fails open
    when no entry exists (so a cache flush never mass-locks users out), which
    means a plain delete would leave the token fully valid. Revocation must
    therefore leave behind an entry whose ``jti`` can never match, keeping
    the original expiry horizon so the poison expires with the token.
    """
    if cache is None or not user_id:
        return
    key = _session_key(user_id, scope)
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


#: Every scope a user can hold a session in ("" = web, plus native classes).
KNOWN_SCOPES = ("", MOBILE_SCOPE)


async def revoke_all_sessions(user_id: str, cache: Optional[CacheProtocol]) -> None:
    """Remove the stored active session in every scope (account deletion,
    password change) so all devices are forced to log in again."""
    for scope in KNOWN_SCOPES:
        await revoke_session(user_id, cache, scope=scope)
