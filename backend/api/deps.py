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
    )


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
            return decode_access_token(token.strip(), settings)
        except AuthenticationError:
            if settings.env != "development":
                raise
    elif settings.env != "development":
        raise AuthenticationError("missing bearer token")

    return _anonymous_payload()
