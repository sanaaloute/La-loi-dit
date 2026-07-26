"""Role-based access control.

Hierarchy (highest first): ADMIN > LEGAL_EXPERT > USER > VIEWER.
`require_role(minimum)` is a FastAPI dependency factory that resolves the
current user (via `backend.api.deps.get_current_user`, imported lazily to
avoid a circular import) and enforces the minimum role.
"""

from __future__ import annotations

from fastapi import Request

from backend.core.exceptions import AuthorizationError
from backend.core.models import Role
from backend.security.jwt import TokenPayload

ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.USER: 1,
    Role.LEGAL_EXPERT: 2,
    Role.ADMIN: 3,
}

# Permission map: logical endpoint capability -> minimum required role.
ENDPOINT_PERMISSIONS: dict[str, Role] = {
    "chat": Role.VIEWER,
    "search": Role.VIEWER,
    "documents:upload": Role.LEGAL_EXPERT,
    "admin": Role.ADMIN,
}


def has_role(actual: Role, minimum: Role) -> bool:
    """Return True when `actual` is at least as privileged as `minimum`."""
    return ROLE_RANK[actual] >= ROLE_RANK[minimum]


def require_role(minimum: Role):
    """FastAPI dependency factory enforcing a minimum role."""

    async def checker(request: Request) -> TokenPayload:
        from backend.api.deps import get_current_user  # lazy: avoids circular import

        user = await get_current_user(request)
        if not has_role(user.role, minimum):
            raise AuthorizationError(
                f"role '{user.role.value}' insufficient: requires at least '{minimum.value}'"
            )
        return user

    return checker
