"""Fixed Afrikia-model descriptors and shared daily usage budgets.

Users cannot select models or replace this catalog at runtime. Legacy tier
names remain only for old tokens and database rows; daily quotas and anti-abuse
rate limits continue to apply to every account.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from pydantic import BaseModel

from backend.core.config import Settings, get_settings
from backend.core.llm import CLASSIFICATION_MODEL, PLANNER_MODEL, SYNTHESIS_MODEL

logger = logging.getLogger(__name__)

TIER_ORDER = ["gratuit", "pro", "cabinet"]
DEFAULT_TIER = "gratuit"


class ModelEntry(BaseModel):
    """One fixed local role model exposed for read-only status."""

    id: str
    provider: str
    label: str = ""


_ALL_PROVIDERS = ["afrikia"]
_ALL_MODELS: list[dict[str, str]] = [
    {"id": f"afrikia/{SYNTHESIS_MODEL}", "provider": "afrikia", "label": "Afrikia synthesis model"},
    {
        "id": f"afrikia/{CLASSIFICATION_MODEL}",
        "provider": "afrikia",
        "label": "Afrikia structured-role model",
    },
    {
        "id": f"afrikia/{PLANNER_MODEL}",
        "provider": "afrikia",
        "label": "Afrikia planner model",
    },
]

_SHARED_CONFIG: dict[str, Any] = {
    "providers": _ALL_PROVIDERS,
    "models": _ALL_MODELS,
    "features": {"export": ["md", "pdf", "word", "csv"], "drafting": True, "priority": True},
    "daily_token_budget": 5_000_000,
    "daily_request_budget": 30,
}
_RATE_LIMITS: dict[str, dict[str, int]] = {
    "gratuit": {"rate_limit_per_minute": 120, "rate_limit_per_second": 3},
    "pro": {"rate_limit_per_minute": 120, "rate_limit_per_second": 3},
    "cabinet": {"rate_limit_per_minute": 600, "rate_limit_per_second": 10},
}
_TIER_CONFIG: dict[str, dict[str, Any]] = {
    tier: {**_SHARED_CONFIG, **_RATE_LIMITS[tier]} for tier in TIER_ORDER
}

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


def _catalog(settings: Optional[Settings] = None) -> dict[str, dict[str, Any]]:
    """Return fixed providers/models with optional rate-limit overrides only."""
    settings = settings or get_settings()
    raw = settings.tier_catalog_json
    if not raw:
        return TIER_CATALOG
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        logger.warning("invalid LEGAL_AI_TIER_CATALOG_JSON; using built-in rate limits")
        return TIER_CATALOG

    catalog: dict[str, dict[str, Any]] = {}
    for tier, cfg in TIER_CATALOG.items():
        override = parsed.get(tier, {})
        catalog[tier] = dict(cfg)
        if not isinstance(override, dict):
            continue
        for field in ("rate_limit_per_minute", "rate_limit_per_second"):
            value = override.get(field)
            if isinstance(value, int) and value > 0:
                catalog[tier][field] = value
        for field in ("daily_token_budget", "daily_request_budget"):
            value = override.get(field)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                catalog[tier][field] = value
    return catalog


# ---------------------------------------------------------------------------
# Admin-adjustable global daily budget
# ---------------------------------------------------------------------------

BUDGET_FIELDS = ("daily_token_budget", "daily_request_budget")

#: app_settings key for one flat JSON object shared by every account. This is
#: deliberately separate from the legacy per-tier ``tier_budgets`` key.
DAILY_BUDGETS_SETTING_KEY = "daily_budgets"

# Module-level cache so hot paths (check_budget, /usage/me) never hit the DB
# on every call. Multi-worker deployments re-read it after a short TTL.
_budget_overrides: dict[str, int] = {}
_budget_overrides_loaded_at = 0.0  # monotonic timestamp; 0 = never loaded
BUDGET_OVERRIDES_TTL_SECONDS = 30.0


def parse_budget_overrides(raw: Optional[str]) -> dict[str, int]:
    """Validate the flat stored budget JSON; ``{}`` on anything unusable."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("invalid daily budget override JSON; ignoring")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        field: int(value)
        for field, value in parsed.items()
        if field in BUDGET_FIELDS
        and isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    }


def set_budget_overrides(overrides: Optional[dict[str, int]]) -> None:
    """Replace the global budget-override cache and reset its TTL clock."""
    global _budget_overrides, _budget_overrides_loaded_at
    _budget_overrides = dict(overrides or {})
    _budget_overrides_loaded_at = time.monotonic()


def budget_overrides_stale() -> bool:
    """True once the cache is older than the TTL (sync, hot-path cheap)."""
    return time.monotonic() - _budget_overrides_loaded_at >= BUDGET_OVERRIDES_TTL_SECONDS


async def refresh_budget_overrides(user_store: Any) -> None:
    """Re-read the global budget when the TTL expires; never raises."""
    global _budget_overrides_loaded_at
    if user_store is None or not budget_overrides_stale():
        return
    try:
        raw = await user_store.get_setting(DAILY_BUDGETS_SETTING_KEY)
        set_budget_overrides(parse_budget_overrides(raw))
    except Exception:
        logger.warning("daily budget override reload failed; keeping cached values", exc_info=True)
        _budget_overrides_loaded_at = time.monotonic()


def get_budget_overrides() -> dict[str, int]:
    """The currently active global overrides (copy)."""
    return dict(_budget_overrides)


def default_daily_budgets(*, settings: Optional[Settings] = None) -> dict[str, int]:
    """Shared daily budgets before the admin override."""
    catalog = _catalog(settings)
    cfg = catalog.get(DEFAULT_TIER, {})
    return {field: int(cfg.get(field, 0) or 0) for field in BUDGET_FIELDS}


def effective_daily_budgets(*, settings: Optional[Settings] = None) -> dict[str, int]:
    """Shared daily budgets as actually enforced (defaults + override)."""
    cfg = get_tier(None, settings=settings)
    return {field: int(cfg.get(field, 0) or 0) for field in BUDGET_FIELDS}


def get_tier(tier: Optional[str], *, settings: Optional[Settings] = None) -> dict[str, Any]:
    """Return shared entitlements/budgets plus legacy anti-abuse rate limits.

    Only the rate-limit fields vary by legacy tier. Features, providers,
    models, and daily budgets always come from the shared default config.
    """
    catalog = _catalog(settings)
    shared = catalog.get(DEFAULT_TIER, {})
    legacy = catalog.get(tier if tier in TIER_ORDER else DEFAULT_TIER, shared)
    cfg = {
        **shared,
        "rate_limit_per_minute": legacy.get("rate_limit_per_minute"),
        "rate_limit_per_second": legacy.get("rate_limit_per_second"),
    }
    if _budget_overrides:
        cfg = {**cfg, **_budget_overrides}
    return cfg


def allowed_models(tier: Optional[str], *, settings: Optional[Settings] = None) -> list[ModelEntry]:
    """Every configured model, deduplicated in catalog order; tier is ignored."""
    catalog = _catalog(settings)
    models: dict[str, ModelEntry] = {}
    for known_tier in TIER_ORDER:
        cfg = catalog.get(known_tier) or (catalog.get(DEFAULT_TIER, {}) if known_tier == DEFAULT_TIER else {})
        for entry in cfg.get("models", []):
            model = ModelEntry(**entry)
            models.setdefault(model.id, model)
    return list(models.values())


def is_model_allowed(tier: Optional[str], model_id: str, *, settings: Optional[Settings] = None) -> bool:
    return any(entry.id == model_id for entry in allowed_models(None, settings=settings))


def find_model(tier: Optional[str], model_id: str, *, settings: Optional[Settings] = None) -> Optional[ModelEntry]:
    """Return any configured catalog entry for `model_id`; tier is ignored."""
    for entry in allowed_models(None, settings=settings):
        if entry.id == model_id:
            return entry
    return None


def default_model(tier: Optional[str], *, settings: Optional[Settings] = None) -> str:
    """The shared default model, preferring Afrikia then the configured provider."""
    models = allowed_models(None, settings=settings)
    if not models:
        return ""
    for entry in models:
        if entry.provider == "afrikia":
            return entry.id
    provider = (settings.llm_provider if settings is not None else "").strip().lower()
    if provider:
        for entry in models:
            if entry.provider == provider:
                return entry.id
    return models[len(models) // 2].id


def all_models_with_access(tier: Optional[str], *, settings: Optional[Settings] = None) -> list[dict[str, Any]]:
    """Every catalog model, always allowed; legacy response shape is retained."""
    return [
        {
            **entry.model_dump(),
            "allowed": True,
            "tier_required": DEFAULT_TIER,
        }
        for entry in allowed_models(None, settings=settings)
    ]
