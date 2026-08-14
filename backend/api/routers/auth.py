"""Auth router: registration + credential login issuing JWTs.

Two account sources, checked in order:
  1. the database user store (``backend.users.UserStore``) — accounts created
     via ``POST /auth/register``;
  2. the dev store (``settings.dev_users``, i.e. the ``LEGAL_AI_DEV_USERS``
     env var, plus the development-only admin/admin123 bootstrap) — kept so
     local dev and existing tooling work unchanged.

DB-user tokens carry ``user_id`` and ``tier`` claims; dev-store tokens get
tier "cabinet" so the local dev admin sees every model.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.core.config import Settings
from backend.core import catalog
from backend.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.core.models import Role
from backend.security.jwt import TokenPayload, create_access_token, decode_access_token
from backend.security.passwords import hash_password, verify_password
from backend.security.sessions import (
    activate_session,
    device_fingerprint,
    generate_jti,
    verify_active_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Tier granted to env-var dev users: local development sees the full catalog.
DEV_USER_TIER = "cabinet"


class TokenRequest(BaseModel):
    username: str  # email for DB users, legacy username for dev-store users
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    role: Role


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)


class MeResponse(BaseModel):
    id: str
    email: str = ""
    name: str = ""
    role: Role
    tier: str = "gratuit"
    workspace_id: str = ""
    workspace_name: str = ""
    features: dict[str, Any] = {}  # tier features from the catalog (export formats, drafting, ...)


def build_user_store(settings: Settings) -> dict[str, dict[str, Any]]:
    """Build the in-memory dev user store (passwords hashed at boot).

    In production the hardcoded admin/admin123 bootstrap is omitted; the admin
    must be provisioned via ``LEGAL_AI_DEV_USERS`` (or another secure bootstrap
    path) so the password never ships in source code. Only one admin entry is
    kept: an explicit ``LEGAL_AI_DEV_USERS`` admin wins over the development
    fallback.
    """
    users: dict[str, dict[str, Any]] = {}

    # Parse env-var bootstrap entries first so they take precedence.
    raw = settings.dev_users
    for entry in raw.split(","):
        parts = [p.strip() for p in entry.split(":")]
        if len(parts) != 3 or not all(parts):
            continue
        username, password, role = parts
        try:
            role_obj = Role(role)
        except ValueError:
            logger.warning("ignoring dev user with unknown role", extra={"username": username, "role": role})
            continue
        if role_obj == Role.ADMIN and any(u["role"] == Role.ADMIN for u in users.values()):
            logger.warning("ignoring extra admin bootstrap entry: only one admin is allowed", extra={"username": username})
            continue
        users[username] = {"password_hash": hash_password(password), "role": role_obj}

    # Development-only fallback admin, only when no admin was bootstrapped.
    if settings.env == "development" and not any(u["role"] == Role.ADMIN for u in users.values()):
        users["admin"] = {"password_hash": hash_password("admin123"), "role": Role.ADMIN}

    return users


def _user_store(request: Request) -> dict[str, dict[str, Any]]:
    store = getattr(request.app.state, "user_store", None)
    if store is None:
        from backend.api.deps import get_ctx

        store = build_user_store(get_ctx(request).settings)
        request.app.state.user_store = store
    return store


def _db_user_store(request: Request):
    from backend.api.deps import get_ctx

    return getattr(get_ctx(request), "user_store", None)


async def _token_response(
    record_id: str,
    email: str,
    role: Role,
    settings: Settings,
    request: Request,
    *,
    user_id: Optional[str],
    tier: str,
) -> TokenResponse:
    """Issue a JWT and, when enabled, bind it as the single active session."""
    from backend.api.deps import get_ctx

    jti = generate_jti()
    token = create_access_token(email or record_id, role, settings, user_id=user_id, tier=tier, jti=jti)
    expires_at = int(time.time()) + settings.jwt_expire_minutes * 60

    if settings.single_session_per_user:
        ctx = get_ctx(request)
        cache = getattr(ctx, "cache", None)
        await activate_session(
            user_id or record_id,
            jti,
            device_fingerprint(request),
            expires_at,
            cache,
        )

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=role,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, request: Request) -> TokenResponse:
    """Create an account (role USER, tier gratuit, personal workspace)."""
    from backend.api.deps import get_ctx

    settings = get_ctx(request).settings
    user_store = _db_user_store(request)
    if user_store is None:
        raise HTTPException(status_code=503, detail="user registration unavailable")
    email = payload.email.lower().strip()
    if "@" not in email:
        raise HTTPException(status_code=422, detail="invalid email address")
    try:
        record = await user_store.create_user(email, payload.password, payload.name)
    except RuntimeError as exc:  # DB down -> dev-user-only mode
        raise HTTPException(status_code=503, detail="user registration unavailable") from exc
    except UserAlreadyExistsError:
        raise
    return await _token_response(record.id, record.email, record.role, settings, request, user_id=record.id, tier=record.tier)


@router.post("/token", response_model=TokenResponse)
async def issue_token(payload: TokenRequest, request: Request) -> TokenResponse:
    """Exchange username/password for a signed JWT.

    DB users authenticate by email first; the env-var dev store (legacy
    usernames) is the fallback, keeping the admin/admin123 dev flow alive.
    """
    from backend.api.deps import get_ctx

    settings = get_ctx(request).settings

    user_store = _db_user_store(request)
    if user_store is not None:
        try:
            record = await user_store.authenticate(payload.username, payload.password)
        except Exception:
            record = None
        if record is not None:
            return await _token_response(record.id, record.email, record.role, settings, request, user_id=record.id, tier=record.tier)

    record = _user_store(request).get(payload.username)
    if record is None or not verify_password(payload.password, record["password_hash"]):
        raise AuthenticationError("invalid credentials")

    return await _token_response(
        payload.username,
        payload.username,
        record["role"],
        settings,
        request,
        user_id=payload.username,
        tier=DEV_USER_TIER,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request) -> TokenResponse:
    """Exchange a valid, non-expired JWT for a fresh one (sliding session).

    The renewed token keeps the same ``jti`` so single-session enforcement is
    preserved and refreshing never kicks out the session. Expired or displaced
    tokens get a 401 — the user must log in again.
    """
    from backend.api.deps import get_ctx

    ctx = get_ctx(request)
    settings = ctx.settings
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise AuthenticationError("missing bearer token")
    payload = decode_access_token(raw.strip(), settings)
    if payload.sub == "anonymous":
        raise AuthenticationError("authentication required")

    session_user = payload.user_id or payload.sub
    cache = getattr(ctx, "cache", None)
    if settings.single_session_per_user and not await verify_active_session(
        session_user, payload.jti, cache
    ):
        raise AuthenticationError("session no longer active")

    jti = payload.jti or generate_jti()
    token = create_access_token(
        session_user, payload.role, settings, user_id=payload.user_id, tier=payload.tier, jti=jti
    )
    expires_at = int(time.time()) + settings.jwt_expire_minutes * 60
    if settings.single_session_per_user:
        await activate_session(session_user, jti, device_fingerprint(request), expires_at, cache)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=payload.role,
    )


@router.get("/me", response_model=MeResponse)
async def whoami(request: Request) -> MeResponse:
    """Return the current account profile (anonymous in development)."""
    from backend.api.deps import get_current_user, get_ctx

    user: TokenPayload = await get_current_user(request)
    settings = get_ctx(request).settings
    if user.user_id:
        user_store = _db_user_store(request)
        record = None
        if user_store is not None:
            try:
                record = await user_store.get_by_id(user.user_id)
            except Exception:
                record = None
        if record is not None:
            workspace_name = ""
            if user_store is not None:
                try:
                    workspace_name = await user_store.get_workspace_name(record.workspace_id)
                except Exception:
                    workspace_name = ""
            return MeResponse(
                id=record.id,
                email=record.email,
                name=record.name,
                role=record.role,
                tier=record.tier,
                workspace_id=record.workspace_id,
                workspace_name=workspace_name,
                features=catalog.get_tier(record.tier, settings=settings).get("features", {}),
            )
    # Dev-store or anonymous principal: synthesize from the token claims.
    return MeResponse(
        id=user.user_id or user.sub,
        email="",
        name="" if user.sub == "anonymous" else user.sub,
        role=user.role,
        tier=user.tier,
        workspace_id="",
        features=catalog.get_tier(user.tier, settings=settings).get("features", {}),
    )
