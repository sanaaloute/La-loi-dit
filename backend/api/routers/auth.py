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
import secrets
import time
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from backend.core.config import Settings
from backend.core import catalog
from backend.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.core.mailer import send_email
from backend.core.models import Role
from backend.security.jwt import TokenPayload, create_access_token, decode_access_token
from backend.security.passwords import hash_password, verify_password
from backend.security.sessions import (
    activate_session,
    device_fingerprint,
    generate_jti,
    revoke_all_sessions,
    revoke_session,
    session_scope,
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
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=32)
    password: str = Field(min_length=8, max_length=200)
    name: str = Field(default="", max_length=200)


class PasswordResetRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)  # email or phone


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


#: Cache namespace + TTL for password-reset tokens.
RESET_KEY_PREFIX = "pwd_reset:"
RESET_TTL_SECONDS = 30 * 60


class MeResponse(BaseModel):
    id: str
    email: str = ""
    phone: str = ""
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
            scope=session_scope(request),
        )

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=role,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(payload: RegisterRequest, request: Request) -> TokenResponse:
    """Create an account (role USER, tier gratuit, personal workspace).

    The login identifier is an email address OR a phone number — at least
    one of the two is required; both may be provided.
    """
    from backend.api.deps import get_ctx
    from backend.users.service import normalize_phone

    settings = get_ctx(request).settings
    user_store = _db_user_store(request)
    if user_store is None:
        raise HTTPException(status_code=503, detail="user registration unavailable")
    email = payload.email.lower().strip()
    phone = normalize_phone(payload.phone) if payload.phone.strip() else ""
    if not email and not phone:
        raise HTTPException(status_code=422, detail="email or phone number required")
    if email and "@" not in email:
        raise HTTPException(status_code=422, detail="invalid email address")
    if payload.phone.strip() and not phone:
        raise HTTPException(status_code=422, detail="invalid phone number")
    try:
        record = await user_store.create_user(email, payload.password, payload.name, phone=phone)
    except RuntimeError as exc:  # DB down -> dev-user-only mode
        raise HTTPException(status_code=503, detail="user registration unavailable") from exc
    except UserAlreadyExistsError:
        raise
    return await _token_response(
        record.id, record.email or record.phone, record.role, settings, request,
        user_id=record.id, tier=record.tier,
    )


@router.post("/token", response_model=TokenResponse)
async def issue_token(payload: TokenRequest, request: Request) -> TokenResponse:
    """Exchange username/password for a signed JWT.

    DB users authenticate by email or phone first; the env-var dev store
    (legacy usernames) is the fallback, keeping the admin/admin123 dev flow
    alive.
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
            return await _token_response(
                record.id, record.email or record.phone, record.role, settings, request,
                user_id=record.id, tier=record.tier,
            )

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
    scope = session_scope(request)
    if settings.single_session_per_user and not await verify_active_session(
        session_user, payload.jti, cache, scope=scope
    ):
        raise AuthenticationError("session no longer active")

    jti = payload.jti or generate_jti()
    token = create_access_token(
        session_user, payload.role, settings, user_id=payload.user_id, tier=payload.tier, jti=jti
    )
    expires_at = int(time.time()) + settings.jwt_expire_minutes * 60
    if settings.single_session_per_user:
        await activate_session(
            session_user, jti, device_fingerprint(request), expires_at, cache, scope=scope
        )

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
                phone=record.phone,
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


@router.post("/logout", status_code=204)
async def logout(request: Request) -> Response:
    """Revoke the caller's active session for this device class.

    Clients must also discard their token; this endpoint makes the discard
    server-side effective immediately (the old token 401s on next use).
    """
    from backend.api.deps import get_ctx, require_user

    user: TokenPayload = await require_user(request)
    ctx = get_ctx(request)
    if ctx.settings.single_session_per_user:
        await revoke_session(
            user.user_id or user.sub,
            getattr(ctx, "cache", None),
            scope=session_scope(request),
        )
    return Response(status_code=204)


@router.delete("/me", status_code=204)
async def delete_account(request: Request) -> Response:
    """Permanently delete the caller's account and associated data.

    Required by app-store rules wherever account creation is offered. Only
    DB-backed accounts can self-delete (dev-store bootstrap accounts are
    deployment-owned); the last admin account is protected so the platform
    can never lock itself out.
    """
    from backend.api.deps import get_ctx, require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="only registered accounts can be deleted")
    ctx = get_ctx(request)
    user_store = _db_user_store(request)
    if user_store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    record = await user_store.get_by_id(user.user_id)
    if record is None:
        # Token belongs to a dev-store bootstrap account, not the DB.
        raise HTTPException(status_code=400, detail="only registered accounts can be deleted")
    if record.role == Role.ADMIN and await user_store.count_admins() <= 1:
        raise HTTPException(status_code=403, detail="the last admin account cannot be deleted")
    if not await user_store.delete_user(record.id):
        raise HTTPException(status_code=500, detail="account deletion failed")
    await revoke_all_sessions(record.id, getattr(ctx, "cache", None))

    # Best-effort purge of chat history + long-term memory; a failure here
    # must not resurrect the account or fail the request.
    memory = getattr(ctx, "memory", None)
    if memory is not None:
        try:
            for entry in await memory.list_sessions(record.id):
                await memory.delete_session(record.id, entry["session_id"])
            memories = await memory.list_memories(record.id)
            await memory.delete_memories([m.id for m in memories])
        except Exception:
            logger.warning(
                "memory purge failed for deleted account", extra={"user_id": record.id}
            )
    return Response(status_code=204)


@router.post("/password-reset/request", status_code=202)
async def request_password_reset(payload: PasswordResetRequest, request: Request) -> Response:
    """Start a password reset. ALWAYS 202 so identifiers cannot be enumerated.

    The reset link is emailed when SMTP is configured and the account has an
    email address. Without a mailer the link is logged in development only.
    Phone-only accounts have no delivery channel yet (no SMS gateway) — the
    request succeeds silently from the client's perspective either way.
    """
    from backend.api.deps import get_ctx

    ctx = get_ctx(request)
    settings = ctx.settings
    cache = getattr(ctx, "cache", None)
    user_store = _db_user_store(request)
    if cache is None or user_store is None:
        raise HTTPException(status_code=503, detail="password reset unavailable")

    try:
        record = await user_store.find_by_identifier(payload.identifier)
    except Exception:
        record = None
    if record is not None:
        token = secrets.token_urlsafe(32)
        await cache.set(f"{RESET_KEY_PREFIX}{token}", {"user_id": record.id}, ttl=RESET_TTL_SECONDS)
        link = f"{settings.frontend_url.rstrip('/')}/reinitialiser?token={token}"
        if record.email:
            sent = await send_email(
                settings,
                record.email,
                "Réinitialisation de votre mot de passe",
                "Bonjour,\n\n"
                "Pour choisir un nouveau mot de passe, ouvrez ce lien "
                f"(valable 30 minutes) :\n\n{link}\n\n"
                "Si vous n'êtes pas à l'origine de cette demande, ignorez ce message.\n",
            )
            if not sent and settings.env == "development":
                logger.info("password reset link for %s: %s", record.email, link)
            elif not sent:
                logger.warning(
                    "password reset email for %s not delivered (mailer disabled/failing)",
                    record.email,
                )
        else:
            logger.info(
                "password reset requested for phone-only account (no delivery channel)",
                extra={"user_id": record.id},
            )
    return Response(status_code=202)


@router.post("/password-reset/confirm")
async def confirm_password_reset(payload: PasswordResetConfirm, request: Request) -> dict[str, str]:
    """Complete a password reset. All existing sessions are revoked, so every
    device must log in again with the new password."""
    from backend.api.deps import get_ctx

    ctx = get_ctx(request)
    cache = getattr(ctx, "cache", None)
    user_store = _db_user_store(request)
    if cache is None or user_store is None:
        raise HTTPException(status_code=503, detail="password reset unavailable")

    key = f"{RESET_KEY_PREFIX}{payload.token}"
    entry = await cache.get(key)
    user_id = entry.get("user_id") if isinstance(entry, dict) else None
    if not user_id:
        raise HTTPException(status_code=400, detail="invalid or expired reset token")
    if not await user_store.set_password(user_id, payload.new_password):
        raise HTTPException(status_code=500, detail="password update failed")
    await cache.delete(key)
    await revoke_all_sessions(user_id, cache)
    return {"detail": "password updated"}


class PreferencesIn(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)


def _memory_store(request: Request):
    from backend.api.deps import get_ctx

    memory = getattr(get_ctx(request), "memory", None)
    if memory is None:
        raise HTTPException(status_code=503, detail="memory store unavailable")
    return memory


@router.get("/me/preferences")
async def get_preferences(request: Request) -> dict[str, Any]:
    """The current user's stored preferences (persona, display choices…)."""
    from backend.api.deps import require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    return {"preferences": await _memory_store(request).get_preferences(user.user_id)}


@router.put("/me/preferences")
async def put_preferences(payload: PreferencesIn, request: Request) -> dict[str, Any]:
    """Replace the current user's preferences (merged by the memory store)."""
    from backend.api.deps import require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    await _memory_store(request).set_preferences(user.user_id, payload.preferences)
    return {"preferences": await _memory_store(request).get_preferences(user.user_id)}


@router.get("/me/memories")
async def list_memories(request: Request) -> dict[str, Any]:
    """What the assistant remembers about the current user (transparency)."""
    from backend.api.deps import require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    records = await _memory_store(request).list_memories(user.user_id)
    return {
        "memories": [
            {
                "id": m.id,
                "kind": m.kind,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "last_accessed": m.last_accessed.isoformat(),
            }
            for m in records[:50]
        ]
    }


@router.delete("/me/memories", status_code=204)
async def erase_memories(request: Request) -> Response:
    """Erase everything the assistant remembers about the current user."""
    from backend.api.deps import require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="registered account required")
    memory = _memory_store(request)
    records = await memory.list_memories(user.user_id)
    await memory.delete_memories([m.id for m in records])
    return Response(status_code=204)
