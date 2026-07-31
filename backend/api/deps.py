"""Shared FastAPI dependencies.

`get_current_user` parses the Bearer JWT. In development mode a missing or
invalid token degrades to an anonymous USER payload instead of a 401, so the
API stays explorable locally; every other environment enforces auth strictly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Request

from backend.core.context import AppContext
from backend.core.exceptions import AuthenticationError
from backend.core.models import Role
from backend.security.jwt import TokenPayload, decode_access_token


def get_ctx(request: Request) -> AppContext:
    """Return the AppContext built during app lifespan."""
    ctx = getattr(request.app.state, "ctx", None)
    if ctx is None:
        raise RuntimeError("application context not initialised (lifespan not run)")
    return ctx


def get_graph(request: Request):
    """Return the compiled LangGraph workflow built during app lifespan."""
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        raise RuntimeError("workflow graph not initialised (lifespan not run)")
    return graph


def _anonymous_payload() -> TokenPayload:
    return TokenPayload(
        sub="anonymous",
        role=Role.USER,
        exp=int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp()),
        tier="gratuit",
    )


async def _refresh_tier(request: Request, payload: TokenPayload) -> TokenPayload:
    """Load the fresh tier from the DB for DB-backed users.

    Falls back to the token claims when the user store is unreachable or the
    account is gone, so a DB outage never breaks authentication.
    """
    if not payload.user_id:
        return payload
    user_store = getattr(get_ctx(request), "user_store", None)
    if user_store is None:
        return payload
    try:
        record = await user_store.get_by_id(payload.user_id)
    except Exception:
        record = None
    if record is not None:
        payload.tier = record.tier
    return payload


async def get_current_user(request: Request) -> TokenPayload:
    """Resolve the current user from the Authorization Bearer token.

    Development mode: missing/invalid token -> anonymous Role.USER payload.
    Any other environment: missing/invalid token -> AuthenticationError (401).
    """
    settings = get_ctx(request).settings
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")

    if scheme.lower() == "bearer" and token:
        try:
            payload = decode_access_token(token.strip(), settings)
            return await _refresh_tier(request, payload)
        except AuthenticationError:
            if settings.env != "development":
                raise
    elif settings.env != "development":
        raise AuthenticationError("missing bearer token")

    return _anonymous_payload()


async def require_user(request: Request) -> TokenPayload:
    """Like `get_current_user` but rejects anonymous callers with a 401.

    Used by endpoints that are meaningless without an account (chat history).
    """
    user = await get_current_user(request)
    if user.sub == "anonymous":
        raise AuthenticationError("authentication required")
    return user
