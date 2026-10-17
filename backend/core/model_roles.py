"""Deterministic per-node-role model resolution.

Nemotron handles bounded classification/planning tasks. Gemma handles legal
analysis and every user-facing free-text answer. There is no request or
environment override: only the test-only mock provider bypasses this mapping.
Role clients share the base client's ``usage_totals`` dict so API token
metering covers every model call in the request.
"""

from __future__ import annotations

import logging

from backend.core.config import Settings
from backend.core.llm import CLASSIFICATION_MODEL, SYNTHESIS_MODEL, LLMClient

logger = logging.getLogger(__name__)

ROLE_MODELS = {
    "classification": CLASSIFICATION_MODEL,
    "planner": CLASSIFICATION_MODEL,
    "analysis": SYNTHESIS_MODEL,
    "synthesis": SYNTHESIS_MODEL,
}


def resolve_role_llm(role: str, base_llm: LLMClient, settings: Settings) -> LLMClient:
    """Return the fixed local Ollama client for one graph role.

    Lightweight custom/mock wrappers without a concrete model identity pass
    through unchanged; fully specified clients always get the fixed role model.
    """
    provider = getattr(base_llm, "provider", "mock")
    base_model = getattr(base_llm, "model", "")
    if provider == "mock" or not base_model:
        return base_llm

    model = ROLE_MODELS.get(role, SYNTHESIS_MODEL)
    if provider == "ollama" and base_model == f"ollama/{model}":
        return base_llm

    role_client = LLMClient(
        settings,
        provider="ollama",
        model=model,
        api_key="",
        api_base=getattr(base_llm, "api_base", "") or settings.llm_api_base,
    )
    role_client.usage_totals = base_llm.usage_totals
    logger.debug("role routing: %s -> %s", role, role_client.model)
    return role_client
