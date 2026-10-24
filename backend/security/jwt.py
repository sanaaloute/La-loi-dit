"""JWT access-token and refresh-token issuance and verification (PyJWT).

Tokens carry the subject (user id), the role and standard iat/exp claims.
Refresh tokens are long-lived and allow clients to request new access tokens
without re-entering credentials. Decoding failures are normalised to
`AuthenticationError` so the API layer can map them to HTTP 401 uniformly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

import jwt

from backend.core.config import Settings
from backend.core.exceptions import AuthenticationError
from backend.core.models import Role
from pydantic import BaseModel


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


class TokenPayload(BaseModel):
    """Validated contents of an access or refresh token."""

    sub: str
    role: Role
    exp: int
    type: TokenType = TokenType.ACCESS
    user_id: Optional[str] = None  # DB user id (None for dev-store users)
    tier: str = "gratuit"  # legacy subscription claim; not an entitlement
    jti: Optional[str] = None  # JWT ID for single-session / refresh enforcement
    device_id: Optional[str] = None  # stable per-device identifier when provided


def _encode(claims: dict, settings: Settings) -> str:
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(
    subject: str,
    role: Role,
    settings: Settings,
    expires_minutes: Optional[int] = None,
    user_id: Optional[str] = None,
    tier: Optional[str] = None,
    jti: Optional[str] = None,
    device_id: Optional[str] = None,
) -> str:
    """Create a signed short-lived access token for `subject` with the given `role`."""
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "role": role.value,
        "type": TokenType.ACCESS.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    if user_id is not None:
        claims["user_id"] = user_id
    if tier is not None:
        claims["tier"] = tier
    if jti is not None:
        claims["jti"] = jti
    if device_id is not None:
        claims["device_id"] = device_id
    return _encode(claims, settings)


def create_refresh_token(
    subject: str,
    role: Role,
    settings: Settings,
    jti: str,
    user_id: Optional[str] = None,
    tier: Optional[str] = None,
    device_id: Optional[str] = None,
) -> str:
    """Create a signed long-lived refresh token for `subject`."""
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "role": role.value,
        "type": TokenType.REFRESH.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_refresh_expire_days)).timestamp()),
        "jti": jti,
    }
    if user_id is not None:
        claims["user_id"] = user_id
    if tier is not None:
        claims["tier"] = tier
    if device_id is not None:
        claims["device_id"] = device_id
    return _encode(claims, settings)


def _decode(token: str, settings: Settings) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("token expired") from exc
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationError(f"invalid token: {exc}") from exc


def decode_access_token(token: str, settings: Settings) -> TokenPayload:
    """Decode and validate an access token.

    Tokens explicitly marked as refresh tokens are rejected. Tokens without a
    type claim are accepted for backward compatibility.
    """
    claims = _decode(token, settings)
    token_type = claims.get("type")
    if token_type is not None and token_type != TokenType.ACCESS.value:
        raise AuthenticationError("invalid token type")
    return TokenPayload(**claims)


def decode_refresh_token(token: str, settings: Settings) -> TokenPayload:
    """Decode and validate a refresh token."""
    claims = _decode(token, settings)
    if claims.get("type") != TokenType.REFRESH.value:
        raise AuthenticationError("invalid token type")
    return TokenPayload(**claims)
