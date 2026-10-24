"""Deterministic per-node-role model resolution.

Qwen 2.5 handles bounded classification/planning tasks. gpt-oss handles legal
analysis and every user-facing free-text answer. There is no request or
environment override: only the test-only mock provider bypasses this mapping.
Role clients share the base client's ``usage_totals`` dict so API token
metering covers every model call in the request.
"""

from __future__ import annotations

import logging

from backend.core.config import Settings
from backend.core.llm import PROVIDER_PREFIXES, ROLE_MODEL_CONFIGS, LLMClient

logger = logging.getLogger(__name__)

ROLE_MODELS = {role: config["model"] for role, config in ROLE_MODEL_CONFIGS.items()}


def resolve_role_llm(role: str, base_llm: LLMClient, settings: Settings) -> LLMClient:
    """Return the fixed Afrikia client for one graph role.

    Lightweight custom/mock wrappers without a concrete model identity pass
    through unchanged; fully specified clients always get the fixed role model.
    """
    provider = getattr(base_llm, "provider", "mock")
    base_model = getattr(base_llm, "model", "")
    if provider == "mock" or not base_model:
        return base_llm

    config = ROLE_MODEL_CONFIGS.get(role, ROLE_MODEL_CONFIGS["synthesis"])
    model = config["model"]
    prefixed = f"{PROVIDER_PREFIXES.get(provider, provider + '/')}{model}"
    if base_model == prefixed and getattr(base_llm, "role_options", {}) == config:
        return base_llm

    role_client = LLMClient(
        settings,
        provider=provider,
        model=model,
        # Empty strings must not block the constructor's settings fallback.
        api_key=getattr(base_llm, "api_key", "") or None,
        api_base=getattr(base_llm, "api_base", "") or None,
        role_options=config,
    )
    role_client.usage_totals = base_llm.usage_totals
    logger.debug("role routing: %s -> %s", role, role_client.model)
    return role_client
