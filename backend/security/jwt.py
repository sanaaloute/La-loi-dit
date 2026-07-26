"""JWT access-token issuance and verification (PyJWT).

Tokens carry the subject (user id), the role and standard iat/exp claims.
Decoding failures are normalised to `AuthenticationError` so the API layer
can map them to HTTP 401 uniformly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from backend.core.config import Settings
from backend.core.exceptions import AuthenticationError
from backend.core.models import Role
from pydantic import BaseModel


class TokenPayload(BaseModel):
    """Validated contents of an access token."""

    sub: str
    role: Role
    exp: int


def create_access_token(
    subject: str,
    role: Role,
    settings: Settings,
    expires_minutes: Optional[int] = None,
) -> str:
    """Create a signed JWT for `subject` with the given `role`."""
    minutes = expires_minutes if expires_minutes is not None else settings.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    claims = {
        "sub": subject,
        "role": role.value,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
    }
    return jwt.encode(claims, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, settings: Settings) -> TokenPayload:
    """Decode and validate a JWT, returning its payload.

    Raises `AuthenticationError` on any invalid, malformed or expired token.
    """
    try:
        claims = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return TokenPayload(**claims)
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("token expired") from exc
    except (jwt.PyJWTError, ValueError) as exc:
        raise AuthenticationError(f"invalid token: {exc}") from exc
