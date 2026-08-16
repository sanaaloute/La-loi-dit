"""Subscription tier catalog — single source of truth for model gating.

Tiers (cumulative): ``gratuit`` < ``pro`` < ``cabinet``. Each tier lists the
providers and models it unlocks, the export formats it may use, and the daily
token/request budgets enforced by the model router. Model ids are namespaced
``"provider/..."`` so the frontend picker and the model router share one
vocabulary.

The whole catalog can be replaced at runtime with the
``LEGAL_AI_TIER_CATALOG_JSON`` env var (JSON with the same shape). Invalid
JSON or a structurally unusable document falls back to the built-in catalog
with a logged warning.

Admins can additionally tune the per-tier daily budgets at runtime (see the
``/admin/settings/tier-budgets`` endpoints): the overrides are persisted in
the users DB (app_settings key ``tier_budgets``) and merged into ``get_tier``
through a module-level cache, so hot paths never touch the DB.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Lowest-to-highest tier order; index = rank.
TIER_ORDER = ["gratuit", "pro", "cabinet"]

DEFAULT_TIER = "gratuit"


class ModelEntry(BaseModel):
    """One selectable model in the catalog."""

    id: str  # namespaced, e.g. "openrouter/deepseek/deepseek-chat"
    provider: str  # logical provider, e.g. "openrouter"
    label: str = ""


# Provider vocabulary shared across the catalog.
_ALL_PROVIDERS = ["ollama", "tokenfree", "openrouter"]
_FREE_PROVIDERS = ["ollama", "openrouter"]

# Full model catalog (pro / cabinet tiers).
_ALL_MODELS: list[dict[str, str]] = [
    # Ollama Cloud models verified against the account's plan
    # (https://ollama.com/cloud). nemotron-3-nano:30b and minimax-m3
    # also work; glm/kimi/deepseek/mistral/qwen3.5 need a paid upgrade.
    {"id": "ollama/gpt-oss:20b", "provider": "ollama", "label": "GPT-OSS 20B (Ollama Cloud)"},
    {"id": "ollama/gpt-oss:120b", "provider": "ollama", "label": "GPT-OSS 120B (Ollama Cloud)"},
    {"id": "ollama/gemma4:31b", "provider": "ollama", "label": "Gemma 4 31B (Ollama Cloud)"},
    # TokenFree chat model ids verified against the account's /v1/models
    # list (https://www.tokenfree.com); the claude-* ids currently return
    # 500 from the TokenFree gateway.
    {"id": "tokenfree/gemini-2.5-flash", "provider": "tokenfree", "label": "Gemini 2.5 Flash (TokenFree)"},
    {"id": "tokenfree/gpt-5.4-mini", "provider": "tokenfree", "label": "GPT-5.4 Mini (TokenFree)"},
    {"id": "tokenfree/qwen-max", "provider": "tokenfree", "label": "Qwen Max (TokenFree)"},
    {"id": "tokenfree/kimi-k2.5", "provider": "tokenfree", "label": "Kimi K2.5 (TokenFree)"},
    # OpenRouter models.
    {"id": "openrouter/deepseek/deepseek-chat", "provider": "openrouter", "label": "DeepSeek Chat"},
    {
        "id": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "provider": "openrouter",
        "label": "Llama 3.3 70B Instruct",
    },
    {
        "id": "openrouter/google/gemini-2.0-flash-001",
        "provider": "openrouter",
        "label": "Gemini 2.0 Flash",
    },
    {"id": "openrouter/openai/gpt-4o", "provider": "openrouter", "label": "GPT-4o"},
    {
        "id": "openrouter/anthropic/claude-sonnet-4",
        "provider": "openrouter",
        "label": "Claude Sonnet 4",
    },
]

# Free-tier catalog: OpenRouter and Ollama only.
_FREE_MODELS: list[dict[str, str]] = [m for m in _ALL_MODELS if m["provider"] in _FREE_PROVIDERS]

# Production quotas. Override via LEGAL_AI_TIER_CATALOG_JSON if needed.
_TIER_CONFIG: dict[str, dict[str, Any]] = {
    "gratuit": {
        "providers": _FREE_PROVIDERS,
        "models": _FREE_MODELS,
        "features": {"export": ["md"], "drafting": False, "priority": False},
        "daily_token_budget": 1_000_000,
        "daily_request_budget": 50,
        "rate_limit_per_minute": 30,
        "rate_limit_per_second": 1,
    },
    "pro": {
        "providers": _ALL_PROVIDERS,
        "models": _ALL_MODELS,
        "features": {"export": ["md", "pdf", "word", "csv"], "drafting": True, "priority": False},
        "daily_token_budget": 10_000_000,
        "daily_request_budget": 500,
        "rate_limit_per_minute": 120,
        "rate_limit_per_second": 3,
    },
    "cabinet": {
        "providers": _ALL_PROVIDERS,
        "models": _ALL_MODELS,
        "features": {"export": ["md", "pdf", "word", "csv"], "drafting": True, "priority": True},
        "daily_token_budget": 100_000_000,
        "daily_request_budget": 10_000,
        "rate_limit_per_minute": 600,
        "rate_limit_per_second": 10,
    },
}

# Dev rate limits (effectively unlimited) used when LEGAL_AI_TIER_CATALOG_JSON
# is empty. The daily budgets above are the real production quotas.
_DEV_RATE_LIMIT_PER_MINUTE = 10_000
_DEV_RATE_LIMIT_PER_SECOND = 10_000

TIER_CATALOG: dict[str, dict[str, Any]] = {
    tier: {
        "providers": cfg["providers"],
        "models": cfg["models"],
        "features": cfg["features"],
        "daily_token_budget": cfg["daily_token_budget"],
        "daily_request_budget": cfg["daily_request_budget"],
        "rate_limit_per_minute": _DEV_RATE_LIMIT_PER_MINUTE,
        "rate_limit_per_second": _DEV_RATE_LIMIT_PER_SECOND,
    }
    for tier, cfg in _TIER_CONFIG.items()
}


def _normalise_tier(tier: Optional[str]) -> str:
    return tier if tier in TIER_ORDER else DEFAULT_TIER


def _validate_catalog(raw: Any) -> Optional[dict[str, dict[str, Any]]]:
    """Minimal structural check: dict of tiers, each with a non-empty models
    list of {id, provider} entries. Returns None when unusable."""
    if not isinstance(raw, dict) or not raw:
        return None
    for tier, cfg in raw.items():
        if not isinstance(tier, str) or not isinstance(cfg, dict):
            return None
        models = cfg.get("models")
        if not isinstance(models, list) or not models:
            return None
        for entry in models:
            if not isinstance(entry, dict) or not entry.get("id") or not entry.get("provider"):
                return None
    return raw


# Cache keyed by the raw JSON string so an env change takes effect without
# a restart of the helper functions (get_settings itself is lru_cached).
_catalog_cache: dict[str, Any] = {"key": None, "catalog": None}


def _catalog(settings: Optional[Settings] = None) -> dict[str, dict[str, Any]]:
    settings = settings or get_settings()
    raw = settings.tier_catalog_json
    if _catalog_cache["key"] == raw and _catalog_cache["catalog"] is not None:
        return _catalog_cache["catalog"]
    catalog: dict[str, dict[str, Any]] = TIER_CATALOG
    if raw:
        try:
            parsed = _validate_catalog(json.loads(raw))
        except Exception:
            parsed = None
        if parsed is not None:
            catalog = parsed
        else:
            logger.warning("invalid LEGAL_AI_TIER_CATALOG_JSON; falling back to the built-in tier catalog")
    _catalog_cache["key"] = raw
    _catalog_cache["catalog"] = catalog
    return catalog


# ---------------------------------------------------------------------------
# Admin-adjustable daily budget overrides
# ---------------------------------------------------------------------------

#: Budget fields an admin may tune per tier.
BUDGET_FIELDS = ("daily_token_budget", "daily_request_budget")

#: app_settings key under which the overrides are persisted (JSON object:
#: ``{"gratuit": {"daily_token_budget": N, ...}, ...}``).
TIER_BUDGETS_SETTING_KEY = "tier_budgets"

# Module-level cache so hot paths (check_budget, /usage/me) never hit the DB
# on every call. Written by set_budget_overrides — called by the admin
# endpoints and once at app startup with the persisted value.
#
# Multi-worker deployments run several uvicorn processes and a PATCH only
# refreshes the serving worker, so the cache carries a short TTL: once stale,
# the next async enforcement point (check_budget) re-reads the persisted
# value via refresh_budget_overrides. get_tier itself stays sync and cheap —
# it serves the cache and never touches the DB.
_budget_overrides: dict[str, dict[str, int]] = {}
_budget_overrides_loaded_at = 0.0  # monotonic timestamp; 0 = never loaded

#: How long a worker may serve its process-local overrides before re-reading
#: the persisted ``tier_budgets`` setting.
BUDGET_OVERRIDES_TTL_SECONDS = 30.0


def parse_budget_overrides(raw: Optional[str]) -> dict[str, dict[str, int]]:
    """Validate the stored overrides JSON; ``{}`` on anything unusable."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("invalid tier budget overrides JSON; ignoring")
        return {}
    if not isinstance(parsed, dict):
        return {}
    overrides: dict[str, dict[str, int]] = {}
    for tier, fields in parsed.items():
        if tier not in TIER_ORDER or not isinstance(fields, dict):
            continue
        valid = {
            field: int(value)
            for field, value in fields.items()
            if field in BUDGET_FIELDS and isinstance(value, int) and not isinstance(value, bool) and value > 0
        }
        if valid:
            overrides[tier] = valid
    return overrides


def set_budget_overrides(overrides: Optional[dict[str, dict[str, int]]]) -> None:
    """Replace the budget-override cache (``None``/``{}`` clears it).

    Explicit writes (admin PATCH, startup load) also reset the TTL clock —
    the serving worker never needs to re-read what it just wrote.
    """
    global _budget_overrides, _budget_overrides_loaded_at
    _budget_overrides = {tier: dict(fields) for tier, fields in (overrides or {}).items()}
    _budget_overrides_loaded_at = time.monotonic()


def budget_overrides_stale() -> bool:
    """True once the cache is older than the TTL (sync, hot-path cheap)."""
    return time.monotonic() - _budget_overrides_loaded_at >= BUDGET_OVERRIDES_TTL_SECONDS


async def refresh_budget_overrides(user_store: Any) -> None:
    """Re-read the persisted overrides when the TTL expired — never raises.

    At most one indexed key read per TTL window: the TTL clock is reset on
    failure too, so a DB outage keeps serving the last known/default values
    instead of being retried on every request.
    """
    global _budget_overrides_loaded_at
    if user_store is None or not budget_overrides_stale():
        return
    try:
        raw = await user_store.get_setting(TIER_BUDGETS_SETTING_KEY)
        set_budget_overrides(parse_budget_overrides(raw))
    except Exception:
        logger.warning("tier budget overrides reload failed; keeping cached values", exc_info=True)
        _budget_overrides_loaded_at = time.monotonic()


def get_budget_overrides() -> dict[str, dict[str, int]]:
    """The currently active overrides (copy)."""
    return {tier: dict(fields) for tier, fields in _budget_overrides.items()}


def default_tier_budgets(*, settings: Optional[Settings] = None) -> dict[str, dict[str, int]]:
    """The catalog's per-tier daily budgets (before any admin override).

    Read from the effective catalog — so a ``LEGAL_AI_TIER_CATALOG_JSON``
    override is reflected in the admin UI "Par défaut" hints too.
    """
    catalog = _catalog(settings)
    return {
        tier: {
            field: int((catalog.get(tier) or catalog.get(DEFAULT_TIER, {})).get(field, 0) or 0)
            for field in BUDGET_FIELDS
        }
        for tier in TIER_ORDER
    }


def effective_tier_budgets(*, settings: Optional[Settings] = None) -> dict[str, dict[str, int]]:
    """Per-tier daily budgets as actually enforced (defaults + overrides)."""
    return {
        tier: {field: int(get_tier(tier, settings=settings).get(field, 0) or 0) for field in BUDGET_FIELDS}
        for tier in TIER_ORDER
    }


def get_tier(tier: Optional[str], *, settings: Optional[Settings] = None) -> dict[str, Any]:
    """Return the catalog config for `tier` (unknown tiers degrade to gratuit).

    Admin budget overrides are merged on top; the returned dict is a copy in
    that case so the cached catalog is never mutated. The override cache is
    process-local with a short TTL — async enforcement points (check_budget)
    re-read the persisted value once it goes stale, so this sync hot path
    never touches the DB itself.
    """
    catalog = _catalog(settings)
    normalised = _normalise_tier(tier)
    cfg = catalog.get(normalised, catalog.get(DEFAULT_TIER, {}))
    overrides = _budget_overrides.get(normalised)
    if overrides:
        cfg = {**cfg, **overrides}
    return cfg


def allowed_models(tier: Optional[str], *, settings: Optional[Settings] = None) -> list[ModelEntry]:
    """Models the tier may select, in catalog order."""
    return [ModelEntry(**entry) for entry in get_tier(tier, settings=settings).get("models", [])]


def is_model_allowed(tier: Optional[str], model_id: str, *, settings: Optional[Settings] = None) -> bool:
    return any(entry.id == model_id for entry in allowed_models(tier, settings=settings))


def find_model(tier: Optional[str], model_id: str, *, settings: Optional[Settings] = None) -> Optional[ModelEntry]:
    """Return the catalog entry for `model_id` when allowed for `tier`."""
    for entry in allowed_models(tier, settings=settings):
        if entry.id == model_id:
            return entry
    return None


def default_model(tier: Optional[str], *, settings: Optional[Settings] = None) -> str:
    """The tier's default model.

    Follows the deployment's default provider (``settings.llm_provider``): its
    first catalog entry wins, so an Ollama-Cloud-first deployment defaults to
    an Ollama Cloud model. Without a provider (or when it has no entry in the
    tier's catalog), the MID option wins — catalog model lists are ordered
    cheap -> premium, and cheap-model routing (see model_router) drops trivial
    queries to the first (cheapest) entry.
    """
    models = allowed_models(tier, settings=settings)
    if not models:
        return ""
    provider = (settings.llm_provider if settings is not None else "").strip().lower()
    if provider:
        for entry in models:
            if entry.provider == provider:
                return entry.id
    return models[len(models) // 2].id


def all_models_with_access(tier: Optional[str], *, settings: Optional[Settings] = None) -> list[dict[str, Any]]:
    """Every catalog model annotated with `allowed` and `tier_required`.

    Drives the frontend picker: users see the full catalogue, with the models
    above their tier marked as locked. `tier_required` is the lowest tier
    whose model list contains the id.
    """
    catalog = _catalog(settings)
    current = _normalise_tier(tier)
    allowed_ids = {entry.id for entry in allowed_models(current, settings=settings)}

    annotated: dict[str, dict[str, Any]] = {}
    for known_tier in TIER_ORDER:
        cfg = catalog.get(known_tier)
        if not cfg:
            continue
        rank = TIER_ORDER.index(known_tier)
        for entry in cfg.get("models", []):
            model_id = entry["id"]
            if model_id in annotated:
                continue  # first (lowest) tier wins for tier_required
            annotated[model_id] = {
                **ModelEntry(**entry).model_dump(),
                "allowed": model_id in allowed_ids,
                "tier_required": known_tier,
            }
    # Insertion order is already lowest-tier-first; keep it stable.
    return list(annotated.values())
