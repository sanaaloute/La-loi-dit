"""Read-only status for the fixed local role models.

Model selection is disabled. This endpoint exists only for compatibility with
clients that previously loaded the catalog; callers cannot change the roles.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend.api.deps import get_ctx
from backend.core import catalog

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    settings = get_ctx(request).settings
    return {
        "default_model": catalog.default_model(None, settings=settings),
        "selectable": False,
        "models": catalog.all_models_with_access(None, settings=settings),
    }
