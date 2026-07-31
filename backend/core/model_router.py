"""Per-request LLM resolution: subscription tier -> gated LLMClient.

Chat requests carry an optional catalog model id. The router checks the
caller's tier against the tier catalog (``backend.core.catalog``) and builds
an ``LLMClient`` for that model; API keys stay server-side (settings).

Cheap-model routing: when the caller did NOT pick a model and the query is
trivially simple (short, single line, no complexity markers), the tier's
FIRST (cheapest) catalog model is used instead of the tier default (the mid
option). Explicit model choices always win.

Offline note: when the process itself runs the deterministic ``mock``
provider (tests, zero-credential local dev), no real provider is reachable,
so gating is still enforced but the resolved client stays the mock
``ctx.llm`` — the app remains runnable with zero external services.

Budget enforcement: ``check_budget`` raises ``QuotaExceededError`` when an
authenticated DB user reaches their tier's daily token budget. Anonymous
and dev-store users (no DB id) are deliberately NOT metered — they get the
gratuit-by-default catalog and are already rate-limited by IP.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.core import catalog
from backend.core.config import Settings
from backend.core.exceptions import AuthorizationError, QuotaExceededError
from backend.core.llm import LLMClient

logger = logging.getLogger(__name__)

DEFAULT_TIER = "gratuit"

# FR complexity markers: their presence means "not a trivial query".
_COMPLEX_MARKERS = ("explique", "analyse", "compare", "rédige", "redige", "contrat")


def _tier_of(user: Any) -> str:
    """Subscription tier for a TokenPayload (anonymous => gratuit)."""
    if user is None or getattr(user, "sub", "anonymous") == "anonymous":
        return DEFAULT_TIER
    return getattr(user, "tier", "") or DEFAULT_TIER


def _provider_api_key(provider: str, settings: Settings) -> str:
    """Server-side key resolution per provider (never client-supplied)."""
    if provider == "openrouter":
        return settings.openrouter_api_key or settings.llm_api_key
    if provider == "tokenfree":
        return settings.tokenfree_api_key or settings.llm_api_key
    return settings.llm_api_key


def is_simple_query(query: str) -> bool:
    """Trivial-query heuristic for cheap-model routing (conservative)."""
    q = query.strip()
    if not q or len(q) > 160 or "\n" in q:
        return False
    lowered = q.lower()
    return not any(marker in lowered for marker in _COMPLEX_MARKERS)


def _auto_model_id(tier: str, query: Optional[str], settings: Settings) -> str:
    """Model id when the caller didn't choose one (cheap routing applies)."""
    models = catalog.allowed_models(tier, settings=settings)
    if not models:
        return ""
    if settings.cheap_routing_enabled and query and is_simple_query(query):
        logger.debug("cheap routing: simple query -> %s (tier %s)", models[0].id, tier)
        return models[0].id  # cheapest: first catalog entry
    return catalog.default_model(tier, settings=settings)


def resolve_model_entry(
    ctx: Any,
    user: Any,
    requested_model: Optional[str] = None,
    query: Optional[str] = None,
) -> Optional[catalog.ModelEntry]:
    """Resolve the gated catalog entry for one request (403 when not allowed)."""
    settings: Settings = ctx.settings
    tier = _tier_of(user)
    if requested_model:
        entry = catalog.find_model(tier, requested_model, settings=settings)
        if entry is None:
            raise AuthorizationError(
                f"model '{requested_model}' requires a higher subscription tier"
            )
        return entry
    return catalog.find_model(tier, _auto_model_id(tier, query, settings), settings=settings)


def resolve_llm(
    ctx: Any,
    user: Any,
    requested_model: Optional[str] = None,
    query: Optional[str] = None,
) -> LLMClient:
    """Resolve the LLMClient for one request, enforcing tier gating.

    - No requested model -> tier default (mid option); trivially simple
      queries route to the tier's cheapest model when enabled.
    - Requested model not allowed for the tier -> AuthorizationError (403).
    - Mock-provider processes keep ``ctx.llm`` (offline mode, see module doc).
    """
    settings: Settings = ctx.settings
    entry = resolve_model_entry(ctx, user, requested_model, query=query)

    if settings.llm_provider == "mock":
        return ctx.llm  # offline mode: gating enforced above, client stays mock
    if entry is None:
        return ctx.llm  # empty catalog for this tier: keep the default client
    return LLMClient(
        settings,
        provider=entry.provider,
        model=entry.id,
        api_key=_provider_api_key(entry.provider, settings),
    )


async def check_budget(user_store: Any, user: Any, settings: Settings) -> None:
    """Raise QuotaExceededError when the caller reached today's token budget.

    Only authenticated DB users (user_id claim) are metered. A metering
    backend outage never blocks the answer path.
    """
    if user is None or not getattr(user, "user_id", None) or user_store is None:
        return
    tier = _tier_of(user)
    budget = catalog.get_tier(tier, settings=settings).get("daily_token_budget")
    if not budget:
        return
    try:
        today = await user_store.get_today_usage(user.user_id)
    except Exception:
        return
    if today.get("tokens_in", 0) + today.get("tokens_out", 0) >= budget:
        raise QuotaExceededError(
            f"Quota journalier de tokens atteint pour votre offre ({tier}). "
            "Passez à l'offre supérieure ou réessayez demain."
        )
