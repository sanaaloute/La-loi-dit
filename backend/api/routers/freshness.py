"""Freshness router: the "Nouveautés" feed of detected official-source changes.

Reads the JSONL store written by the lifespan freshness loop
(``backend.ingestion.freshness`` + ``LEGAL_AI_FRESHNESS_CHECK_ENABLED``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from backend.api.deps import get_ctx
from backend.core.models import Role
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

router = APIRouter(prefix="/freshness", tags=["freshness"])


class FreshnessEvent(BaseModel):
    source_name: str
    url: str
    kind: str
    detected_at: datetime
    detail: str = ""
    metadata: dict[str, Any] = {}


@router.get("/events", response_model=list[FreshnessEvent])
async def list_freshness_events(
    request: Request,
    limit: int = 50,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> list[FreshnessEvent]:
    """Newest-first detected changes (new laws, updated official pages)."""
    from backend.ingestion.freshness import read_events

    ctx = get_ctx(request)
    events = read_events(ctx.settings.data_dir, limit=max(1, min(limit, 200)))
    return [FreshnessEvent(**e.model_dump()) for e in events]
