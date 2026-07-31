"""Models router: expose the tier catalog to the frontend model picker.

Bearer optional — anonymous callers see the gratuit catalog. Every catalog
model is returned with an `allowed` flag and the tier that unlocks it, so
the UI can render the full catalogue with locked entries.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend.api.deps import get_ctx, get_current_user
from backend.core import catalog
from backend.core.exceptions import AuthenticationError

router = APIRouter(tags=["models"])

_ANONYMOUS_TIER = "gratuit"


async def _caller_tier(request: Request) -> str:
    """Tier of the current caller (DB-refreshed); anonymous => gratuit."""
    try:
        user = await get_current_user(request)
    except AuthenticationError:
        return _ANONYMOUS_TIER
    if user.sub == "anonymous":
        return _ANONYMOUS_TIER
    return user.tier or _ANONYMOUS_TIER


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    """Return the catalog annotated with per-model access for the caller."""
    settings = get_ctx(request).settings
    tier = await _caller_tier(request)
    return {
        "default_model": catalog.default_model(tier, settings=settings),
        "models": catalog.all_models_with_access(tier, settings=settings),
    }
