"""Auth router: dev-bootstrap credential login issuing JWTs.

User accounts come from the LEGAL_AI_DEV_USERS env var
("user:pass:role,user2:pass2:role2"). A default admin/admin123/admin account
exists ONLY when settings.env == "development". Passwords are hashed with
bcrypt once at boot. This is a development bootstrap, not a production IdP.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.exceptions import AuthenticationError
from backend.core.models import Role
from backend.security.jwt import TokenPayload, create_access_token
from backend.security.passwords import hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    role: Role


def build_user_store(settings: Settings) -> dict[str, dict[str, Any]]:
    """Build the in-memory dev user store (passwords hashed at boot)."""
    users: dict[str, dict[str, Any]] = {}
    if settings.env == "development":
        users["admin"] = {"password_hash": hash_password("admin123"), "role": Role.ADMIN}

    raw = os.environ.get("LEGAL_AI_DEV_USERS", "")
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            continue
        username, password, role = parts
        try:
            users[username] = {"password_hash": hash_password(password), "role": Role(role)}
        except ValueError:
            logger.warning("ignoring dev user with unknown role", extra={"username": username, "role": role})
    return users


def _user_store(request: Request) -> dict[str, dict[str, Any]]:
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        from backend.api.deps import get_ctx

        store = build_user_store(get_ctx(request).settings)
        request.app.state.user_store = store
    return store


@router.post("/token", response_model=TokenResponse)
async def issue_token(payload: TokenRequest, request: Request) -> TokenResponse:
    """Exchange username/password for a signed JWT."""
    from backend.api.deps import get_ctx

    settings = get_ctx(request).settings
    record = _user_store(request).get(payload.username)
    if record is None or not verify_password(payload.password, record["password_hash"]):
        raise AuthenticationError("invalid credentials")

    token = create_access_token(payload.username, record["role"], settings)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=record["role"],
    )


@router.get("/me", response_model=TokenPayload)
async def whoami(request: Request) -> TokenPayload:
    """Return the current token payload (anonymous in development)."""
    from backend.api.deps import get_current_user

    return await get_current_user(request)
