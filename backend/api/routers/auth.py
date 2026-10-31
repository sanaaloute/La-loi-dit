"""Auth router: registration + credential login issuing JWTs.

Two account sources, checked in order:
  1. the database user store (``backend.users.UserStore``) — accounts created
     via ``POST /auth/register``;
  2. the dev store (``settings.dev_users``, i.e. the ``LEGAL_AI_DEV_USERS``
     env var, plus the development-only admin/admin123 bootstrap) — kept so
     local dev and existing tooling work unchanged.

DB-user tokens carry ``user_id`` and legacy ``tier`` claims; dev-store tokens
use "cabinet" for compatibility. Neither affects feature or model access.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.core.config import Settings
from pathlib import Path
from backend.core import catalog
from backend.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.core.mailer import send_email
from backend.core.models import Role
from backend.security.jwt import (
    TokenPayload,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
)
from backend.security.passwords import hash_password, verify_password
from backend.security.sessions import (
    activate_session,
    device_fingerprint,
    generate_jti,
    revoke_all_sessions,
    revoke_refresh_tokens,
    revoke_session,
    session_device_id,
    store_refresh_token,
    verify_active_session,
    verify_refresh_token,
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
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    role: Role


class RefreshRequest(BaseModel):
    refresh_token: str


class RegisterRequest(BaseModel):
    email: str = Field(default="", max_length=320)
    phone: str = Field(default="", max_length=32)
    password: str = Field(min_length=6, max_length=200)
    name: str = Field(default="", max_length=200)


class PasswordResetRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)  # email or phone


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new_password: str = Field(min_length=6, max_length=200)


class ProfileUpdate(BaseModel):
    """Editable profile fields; the current password gates every change."""

    current_password: str = Field(min_length=1, max_length=200)
    name: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=32)
    email: Optional[str] = Field(default=None, max_length=320)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=6, max_length=200)


#: Profile photos live under <data_dir>/avatars/<user_id>.<ext>; large phone
#: photos are expected, so the cap is generous (nginx allows 100 MB).
AVATAR_MAX_BYTES = 10 * 1024 * 1024
AVATAR_CONTENT_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
#: Filename-extension fallback when the upload carries no useful MIME type
#: (the expo-file-system File part does not always set one).
AVATAR_EXTENSIONS = {".jpg": ".jpg", ".jpeg": ".jpg", ".png": ".png", ".webp": ".webp"}


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
    avatar_url: str = ""  # /api/v1/auth/me/avatar?v=<mtime> when a photo is set


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


def _avatar_dir(settings: Settings) -> Path:
    return settings.ensure_data_dir() / "avatars"


def _avatar_file(settings: Settings, user_id: str) -> Optional[Path]:
    """The stored photo for a user (any supported extension), else None."""
    avatar_dir = _avatar_dir(settings)
    for ext in AVATAR_CONTENT_TYPES.values():
        candidate = avatar_dir / f"{user_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _avatar_url(settings: Settings, user_id: str) -> str:
    """Cache-busting URL of the caller's photo ("" when none is stored)."""
    existing = _avatar_file(settings, user_id)
    if existing is None:
        return ""
    return f"/api/v1/auth/me/avatar?v={int(existing.stat().st_mtime)}"


async def _check_current_password(user_store: Any, record: Any, current_password: str) -> None:
    """403 unless the current password matches the account's stored hash."""
    identifier = record.email or record.phone
    ok = False
    if identifier:
        try:
            ok = await user_store.authenticate(identifier, current_password) is not None
        except Exception:
            ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Le mot de passe actuel est incorrect.")


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
    """Issue an access token, a refresh token and bind them to this device."""
    from backend.api.deps import get_ctx

    session_user = user_id or record_id
    device_id = session_device_id(request)
    jti = generate_jti()
    access_token = create_access_token(
        email or record_id, role, settings, user_id=user_id, tier=tier, jti=jti, device_id=device_id
    )
    access_expires_at = int(time.time()) + settings.jwt_expire_minutes * 60

    refresh_jti = generate_jti()
    refresh_token = create_refresh_token(
        email or record_id, role, settings, jti=refresh_jti, user_id=user_id, tier=tier, device_id=device_id
    )
    refresh_expires_at = int(time.time()) + settings.jwt_refresh_expire_days * 24 * 3600

    ctx = get_ctx(request)
    cache = getattr(ctx, "cache", None)
    if settings.single_session_per_user:
        await activate_session(
            session_user,
            jti,
            device_fingerprint(request),
            access_expires_at,
            cache,
            device_id=device_id,
        )
    await store_refresh_token(
        session_user,
        refresh_jti,
        device_id,
        refresh_expires_at,
        cache,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
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
async def refresh_token(
    request: Request,
    payload: Optional[RefreshRequest] = None,
) -> TokenResponse:
    """Exchange a refresh token for a new access token.

    The preferred flow sends the long-lived ``refresh_token`` in the JSON body;
    the legacy flow (for old clients) sends a still-valid access token in the
    ``Authorization`` header as a sliding session. Either way, a new access
    token is issued and bound to the device identified by ``X-Device-Id``.
    """
    from backend.api.deps import get_ctx

    ctx = get_ctx(request)
    settings = ctx.settings
    cache = getattr(ctx, "cache", None)
    device_id = session_device_id(request)

    # --- preferred refresh-token flow ---
    if payload is not None and payload.refresh_token:
        try:
            refresh_payload = decode_refresh_token(payload.refresh_token, settings)
        except AuthenticationError:
            raise AuthenticationError("invalid refresh token")
        if refresh_payload.sub == "anonymous":
            raise AuthenticationError("authentication required")

        session_user = refresh_payload.user_id or refresh_payload.sub
        if not await verify_refresh_token(
            session_user, refresh_payload.jti or "", device_id, cache
        ):
            raise AuthenticationError("session no longer active")
        # Re-bind to the current device if the client moved to a new browser/app
        # installation without invalidating the old token (defence in depth).
        if device_id and refresh_payload.device_id and refresh_payload.device_id != device_id:
            raise AuthenticationError("device mismatch")

        access_jti = generate_jti()
        access_token = create_access_token(
            session_user,
            refresh_payload.role,
            settings,
            user_id=refresh_payload.user_id,
            tier=refresh_payload.tier,
            jti=access_jti,
            device_id=device_id,
        )
        access_expires_at = int(time.time()) + settings.jwt_expire_minutes * 60
        if settings.single_session_per_user:
            await activate_session(
                session_user,
                access_jti,
                device_fingerprint(request),
                access_expires_at,
                cache,
                device_id=device_id,
            )
        return TokenResponse(
            access_token=access_token,
            refresh_token=payload.refresh_token,
            expires_in=settings.jwt_expire_minutes * 60,
            role=refresh_payload.role,
        )

    # --- legacy sliding-session flow (access token in Authorization header) ---
    header = request.headers.get("authorization", "")
    scheme, _, raw = header.partition(" ")
    if scheme.lower() != "bearer" or not raw.strip():
        raise AuthenticationError("missing refresh token")
    access_payload = decode_access_token(raw.strip(), settings)
    if access_payload.sub == "anonymous":
        raise AuthenticationError("authentication required")

    session_user = access_payload.user_id or access_payload.sub
    if settings.single_session_per_user and not await verify_active_session(
        session_user, access_payload.jti, cache, device_id=device_id
    ):
        raise AuthenticationError("session no longer active")

    access_jti = generate_jti()
    access_token = create_access_token(
        session_user,
        access_payload.role,
        settings,
        user_id=access_payload.user_id,
        tier=access_payload.tier,
        jti=access_jti,
        device_id=device_id,
    )
    access_expires_at = int(time.time()) + settings.jwt_expire_minutes * 60
    refresh_jti = generate_jti()
    refresh_token = create_refresh_token(
        session_user,
        access_payload.role,
        settings,
        jti=refresh_jti,
        user_id=access_payload.user_id,
        tier=access_payload.tier,
        device_id=device_id,
    )
    refresh_expires_at = int(time.time()) + settings.jwt_refresh_expire_days * 24 * 3600
    if settings.single_session_per_user:
        await activate_session(
            session_user,
            access_jti,
            device_fingerprint(request),
            access_expires_at,
            cache,
            device_id=device_id,
        )
    await store_refresh_token(
        session_user,
        refresh_jti,
        device_id,
        refresh_expires_at,
        cache,
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_expire_minutes * 60,
        role=access_payload.role,
    )


@router.get("/me", response_model=MeResponse)
async def whoami(request: Request) -> MeResponse:
    """Return the authenticated account profile, rejecting suspended accounts."""
    from backend.api.deps import get_ctx, require_user

    user: TokenPayload = await require_user(request)
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
                avatar_url=_avatar_url(settings, record.id),
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
    """Revoke the caller's active session and refresh tokens.

    Clients must also discard their tokens locally; this endpoint makes the
    discard server-side effective immediately.
    """
    from backend.api.deps import get_ctx, require_user

    user: TokenPayload = await require_user(request)
    ctx = get_ctx(request)
    session_user = user.user_id or user.sub
    cache = getattr(ctx, "cache", None)
    device_id = session_device_id(request)
    if ctx.settings.single_session_per_user:
        await revoke_session(session_user, cache, device_id=device_id)
    await revoke_refresh_tokens(session_user, cache)
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
    existing_avatar = _avatar_file(ctx.settings, record.id)
    if existing_avatar is not None:
        try:
            existing_avatar.unlink()
        except OSError:
            pass  # a leftover photo must not fail the deletion

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


@router.patch("/me", response_model=MeResponse)
async def update_profile(payload: ProfileUpdate, request: Request) -> MeResponse:
    """Update the caller's name/phone/email, gated on the current password."""
    from backend.api.deps import require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="only registered accounts can edit their profile")
    user_store = _db_user_store(request)
    if user_store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    record = await user_store.get_by_id(user.user_id)
    if record is None:
        raise HTTPException(status_code=400, detail="only registered accounts can edit their profile")
    await _check_current_password(user_store, record, payload.current_password)
    try:
        updated = await user_store.update_profile(
            record.id,
            name=record.name if payload.name is None else payload.name,
            phone=record.phone if payload.phone is None else payload.phone,
            email=record.email if payload.email is None else payload.email,
        )
    except ValueError as exc:
        detail = (
            "Cette adresse email est déjà utilisée."
            if str(exc) == "email_taken"
            else "Ce numéro de téléphone est déjà utilisé."
        )
        raise HTTPException(status_code=409, detail=detail) from exc
    if not updated:
        raise HTTPException(status_code=500, detail="profile update failed")
    return await whoami(request)


@router.post("/me/password", status_code=204)
async def change_password(payload: PasswordChange, request: Request) -> Response:
    """Change the caller's password after confirming the current one."""
    from backend.api.deps import require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="only registered accounts can change password")
    user_store = _db_user_store(request)
    if user_store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    record = await user_store.get_by_id(user.user_id)
    if record is None:
        raise HTTPException(status_code=400, detail="only registered accounts can change password")
    await _check_current_password(user_store, record, payload.current_password)
    if not await user_store.set_password(record.id, payload.new_password):
        raise HTTPException(status_code=500, detail="password change failed")
    return Response(status_code=204)


@router.post("/me/avatar")
async def upload_avatar(request: Request, file: UploadFile) -> dict[str, str]:
    """Store the caller's profile photo (jpeg/png/webp, ≤ 10 MB)."""
    from backend.api.deps import get_ctx, require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=400, detail="only registered accounts can set a photo")
    user_store = _db_user_store(request)
    if user_store is None or await user_store.get_by_id(user.user_id) is None:
        raise HTTPException(status_code=400, detail="only registered accounts can set a photo")
    ext = AVATAR_CONTENT_TYPES.get((file.content_type or "").lower())
    if ext is None:
        ext = AVATAR_EXTENSIONS.get(Path(file.filename or "").suffix.lower())
    if ext is None:
        raise HTTPException(status_code=400, detail="Format d'image non pris en charge (jpeg, png, webp).")
    data = await file.read()
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="La photo dépasse la taille maximale autorisée (10 Mo).")
    if not data:
        raise HTTPException(status_code=400, detail="Le fichier est vide.")
    settings = get_ctx(request).settings
    avatar_dir = _avatar_dir(settings)
    avatar_dir.mkdir(parents=True, exist_ok=True)
    # One file per user: drop any previous extension before writing.
    for old_ext in AVATAR_CONTENT_TYPES.values():
        old = avatar_dir / f"{user.user_id}{old_ext}"
        if old.is_file() and old_ext != ext:
            old.unlink()
    target = avatar_dir / f"{user.user_id}{ext}"
    tmp = target.with_suffix(f"{ext}.tmp")
    tmp.write_bytes(data)
    tmp.replace(target)
    return {"avatar_url": _avatar_url(settings, user.user_id)}


@router.get("/me/avatar")
async def get_avatar(request: Request) -> FileResponse:
    """Serve the caller's profile photo; 404 when none is stored."""
    from backend.api.deps import get_ctx, require_user

    user: TokenPayload = await require_user(request)
    if not user.user_id:
        raise HTTPException(status_code=404, detail="no photo")
    existing = _avatar_file(get_ctx(request).settings, user.user_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="no photo")
    return FileResponse(existing, headers={"Cache-Control": "no-cache"})


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
