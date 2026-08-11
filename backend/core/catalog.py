"""Subscription tier catalog — single source of truth for model gating.

Tiers (cumulative): ``gratuit`` < ``pro`` < ``cabinet``. Each tier lists the
providers and models it unlocks, the export formats it may use, and a daily
token budget (metering lands in a later phase). Model ids are namespaced
``"provider/..."`` so the frontend picker and the model router share one
vocabulary.

The whole catalog can be replaced at runtime with the
``LEGAL_AI_TIER_CATALOG_JSON`` env var (JSON with the same shape). Invalid
JSON or a structurally unusable document falls back to the built-in catalog
with a logged warning.
"""

from __future__ import annotations

import json
import logging
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


# DEVELOPMENT MODE: every tier currently unlocks the full model catalog with
# effectively unlimited token budgets and rate limits — usage is NOT metered
# down per tier while the system is being built. Before production deployment,
# re-introduce per-tier model lists, budgets and rate limits here.
_ALL_PROVIDERS = ["ollama", "tokenfree", "openrouter"]

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

_DEV_DAILY_TOKEN_BUDGET = 100_000_000  # dev: effectively unlimited
_DEV_RATE_LIMIT_PER_MINUTE = 10_000  # dev: effectively unlimited

TIER_CATALOG: dict[str, dict[str, Any]] = {
    tier: {
        "providers": _ALL_PROVIDERS,
        "models": _ALL_MODELS,
        "features": {"export": ["md", "pdf", "word", "csv"], "drafting": True, "priority": True},
        "daily_token_budget": _DEV_DAILY_TOKEN_BUDGET,
        "rate_limit_per_minute": _DEV_RATE_LIMIT_PER_MINUTE,
    }
    for tier in TIER_ORDER
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


def get_tier(tier: Optional[str], *, settings: Optional[Settings] = None) -> dict[str, Any]:
    """Return the catalog config for `tier` (unknown tiers degrade to gratuit)."""
    catalog = _catalog(settings)
    return catalog.get(_normalise_tier(tier), catalog.get(DEFAULT_TIER, {}))


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
    """The tier's default model — the MID catalog option.

    Catalog model lists are ordered cheap -> premium; the default sits in
    the middle, and cheap-model routing (see model_router) drops trivial
    queries to the first (cheapest) entry.
    """
    models = allowed_models(tier, settings=settings)
    return models[len(models) // 2].id if models else ""


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
