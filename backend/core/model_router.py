"""Fixed local-model resolution and shared daily-budget enforcement.

The default request model is Gemma. Structured graph roles are assigned by
``backend.core.model_roles``. Caller-supplied model IDs are ignored: there is
no catalog selection, cheap-model routing, or external provider failover.
Offline mock processes keep ``ctx.llm`` for deterministic tests.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.core import catalog
from backend.core.config import Settings
from backend.core.exceptions import QuotaExceededError
from backend.core.llm import SYNTHESIS_MODEL, LLMClient

DEFAULT_TIER = "gratuit"


def resolve_model_entry(
    ctx: Any,
    user: Any,
    requested_model: Optional[str] = None,
    query: Optional[str] = None,
) -> Optional[catalog.ModelEntry]:
    """Return the fixed synthesis model; legacy selection inputs are inert."""
    _ = (user, requested_model, query)
    settings: Settings = ctx.settings
    return catalog.find_model(None, f"ollama/{SYNTHESIS_MODEL}", settings=settings)


def resolve_llm(
    ctx: Any,
    user: Any,
    requested_model: Optional[str] = None,
    query: Optional[str] = None,
) -> LLMClient:
    """Build the fixed Gemma client, or preserve the injected mock client."""
    settings: Settings = ctx.settings
    if settings.llm_provider.lower() == "mock":
        return ctx.llm
    entry = resolve_model_entry(ctx, user, requested_model, query=query)
    if entry is None:
        return ctx.llm
    return LLMClient(
        settings,
        provider="ollama",
        model=entry.id,
        api_key="",
        api_base=settings.llm_api_base,
    )


async def check_budget(user_store: Any, user: Any, settings: Settings) -> None:
    """Raise QuotaExceededError when the caller reached today's budget.

    Enforces both the daily token budget and the daily request count. Only
    authenticated DB users (user_id claim) are metered. A metering backend
    outage never blocks the answer path.
    """
    if user is None or not getattr(user, "user_id", None) or user_store is None:
        return
    await catalog.refresh_budget_overrides(user_store)
    tier_cfg = catalog.get_tier(None, settings=settings)
    token_budget = tier_cfg.get("daily_token_budget")
    request_budget = tier_cfg.get("daily_request_budget")
    try:
        today = await user_store.get_today_usage(user.user_id)
    except Exception:
        return
    tokens = today.get("tokens_in", 0) + today.get("tokens_out", 0)
    requests = today.get("requests", 0)
    if token_budget and tokens >= token_budget:
        raise QuotaExceededError("Quota journalier de tokens atteint. Réessayez demain.")
    if request_budget and requests >= request_budget:
        raise QuotaExceededError("Quota journalier de requêtes atteint. Réessayez demain.")
