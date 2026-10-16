"""Per-request LLM resolution and shared daily-budget enforcement.

Every authenticated user may select any model in ``backend.core.catalog``;
legacy tier claims do not restrict access. API keys stay server-side. When no
model is selected, cheap-model routing sends trivially simple queries to the
first catalog entry and other queries to the shared default.

Offline mock processes keep ``ctx.llm`` so tests and zero-credential local
development remain runnable. ``check_budget`` enforces the same daily token
and request budgets for every metered account. Anonymous callers without a
user id are not metered and remain rate-limited by IP.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.core import catalog
from backend.core.config import Settings
from backend.core.exceptions import QuotaExceededError
from backend.core.llm import FailoverLLMClient, LLMClient

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
    if provider == "ollama":
        return settings.ollama_api_key or settings.llm_api_key
    return settings.llm_api_key


def _first_catalog_model(provider: str, settings: Settings) -> str:
    """First catalog model id for a provider, scanning tiers cheap -> premium."""
    for tier in catalog.TIER_ORDER:
        for entry in catalog.get_tier(tier, settings=settings).get("models", []):
            if entry.get("provider") == provider:
                return entry["id"]
    return ""


def with_failover(client: LLMClient, settings: Settings) -> LLMClient:
    """Wrap `client` in a failover chain over the other configured providers.

    Providers listed in ``llm_fallback_providers`` are appended in order,
    using their first catalog model; providers without an API key are skipped.
    Returns `client` unchanged for the mock provider or when no fallback is
    available, so offline/test behavior is untouched.
    """
    if settings.llm_provider.lower() == "mock":
        return client
    clients = [client]
    for provider in (p.strip() for p in settings.llm_fallback_providers.split(",")):
        if not provider or provider == client.provider:
            continue
        api_key = _provider_api_key(provider, settings)
        model_id = _first_catalog_model(provider, settings)
        if not api_key or not model_id:
            continue
        clients.append(LLMClient(settings, provider=provider, model=model_id, api_key=api_key))
    return FailoverLLMClient(clients) if len(clients) > 1 else client


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
    """Resolve any configured model; stale unknown ids fall back to the default."""
    settings: Settings = ctx.settings
    tier = _tier_of(user)
    if requested_model:
        entry = catalog.find_model(None, requested_model, settings=settings)
        if entry is not None:
            return entry
        logger.warning(
            "requested model %r is no longer in the catalog; falling back to the default",
            requested_model,
        )
    return catalog.find_model(None, _auto_model_id(tier, query, settings), settings=settings)


def resolve_llm(
    ctx: Any,
    user: Any,
    requested_model: Optional[str] = None,
    query: Optional[str] = None,
) -> LLMClient:
    """Build the selected LLM client without subscription-based restrictions."""
    settings: Settings = ctx.settings
    entry = resolve_model_entry(ctx, user, requested_model, query=query)

    if settings.llm_provider == "mock":
        return ctx.llm
    if entry is None:
        return ctx.llm
    resolved = LLMClient(
        settings,
        provider=entry.provider,
        model=entry.id,
        api_key=_provider_api_key(entry.provider, settings),
    )
    return with_failover(resolved, settings)


async def check_budget(user_store: Any, user: Any, settings: Settings) -> None:
    """Raise QuotaExceededError when the caller reached today's budget.

    Enforces both the daily token budget and the daily request count. Only
    authenticated DB users (user_id claim) are metered. A metering backend
    outage never blocks the answer path.
    """
    if user is None or not getattr(user, "user_id", None) or user_store is None:
        return
    # Pick up admin budget changes made on another uvicorn worker (TTL-bound,
    # never raises — see catalog.refresh_budget_overrides).
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
