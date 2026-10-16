"""Usage router: per-user consumption against the shared daily budgets."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request

from backend.api.deps import get_ctx, require_user
from backend.core import catalog
from backend.security.jwt import TokenPayload

router = APIRouter(prefix="/usage", tags=["usage"])

_EMPTY_TODAY: dict[str, Any] = {"tokens_in": 0, "tokens_out": 0, "requests": 0}


@router.get("/me")
async def usage_me(
    request: Request,
    user: TokenPayload = Depends(require_user),
) -> dict[str, Any]:
    """Return today's usage, both remaining budgets, and 30-day history."""
    ctx = get_ctx(request)
    tier = user.tier or "gratuit"
    config = catalog.get_tier(None, settings=ctx.settings)
    token_budget = config.get("daily_token_budget", 0)
    request_budget = config.get("daily_request_budget", 0)

    history: list[dict[str, Any]] = []
    if user.user_id and ctx.user_store is not None:
        history = await ctx.user_store.get_usage(user.user_id, days=30)

    today = dict(_EMPTY_TODAY)
    today_iso = date.today().isoformat()
    if history and history[0].get("day") == today_iso:
        today = {k: history[0][k] for k in _EMPTY_TODAY}

    tokens_used = today["tokens_in"] + today["tokens_out"]
    return {
        "tier": tier,
        "daily_budget": token_budget,
        "daily_token_budget": token_budget,
        "daily_request_budget": request_budget,
        "today": today,
        "remaining_tokens": max(0, token_budget - tokens_used),
        "remaining_requests": max(0, request_budget - today["requests"]),
        "history": history,
    }
