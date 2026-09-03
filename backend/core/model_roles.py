"""Per-node-role model resolution (spec §46: the strongest model is NOT
called at every node).

When ``model_role_routing_enabled`` is on, each graph node's LLM calls are
resolved through ``resolve_role_llm``: nodes with a configured role override
(``planner_model``, ``classification_model``, ``analysis_model``,
``synthesis_model``) get a client bound to that model on the SAME provider as
the request's resolved model; every other node keeps the request's client
untouched. Intended mapping:

    classification (cheap) -> context_agent, memory_agent
    planner (cheap)        -> planner
    analysis               -> reasoning_agent, reflection_agent
    synthesis (strong)     -> response_generator (final answer)

Token metering: role clients share the base client's ``usage_totals`` dict,
so the API layer's per-request delta metering (chat.py) sees the tokens of
every role without any aggregation change.

Offline note: with the mock provider the model name is never sent anywhere,
so role routing returns the base client unchanged — a strict no-op, matching
``model_router.resolve_llm``/``with_failover`` mock behavior.
"""

from __future__ import annotations

import logging
from typing import Optional, Union

from backend.core.config import Settings
from backend.core.llm import FailoverLLMClient, LLMClient

logger = logging.getLogger(__name__)

# role name -> Settings attribute holding the optional model override
ROLE_MODEL_ATTRS = {
    "planner": "planner_model",
    "classification": "classification_model",
    "analysis": "analysis_model",
    "synthesis": "synthesis_model",
}

AnyLLM = Union[LLMClient, FailoverLLMClient]


def role_model_override(role: str, settings: Settings) -> Optional[str]:
    """Configured model override for a role (None when unset/unknown role)."""
    attr = ROLE_MODEL_ATTRS.get(role, "")
    if not attr:
        return None
    override = getattr(settings, attr, None)
    return override or None


def resolve_role_llm(role: str, base_llm: AnyLLM, settings: Settings) -> AnyLLM:
    """Return the LLM client for one node role.

    Returns `base_llm` unchanged unless role routing is enabled AND the role
    has a model override AND the provider actually uses model names (mock is
    a no-op). A FailoverLLMClient base keeps its fallback chain with the
    role-model client as the new primary.
    """
    if not settings.model_role_routing_enabled:
        return base_llm
    override = role_model_override(role, settings)
    if not override:
        return base_llm
    if base_llm.provider == "mock":
        return base_llm  # offline mode: model names are inert, stay a no-op

    primary = base_llm.primary if isinstance(base_llm, FailoverLLMClient) else base_llm
    # Optional split-serving: role overrides can target a different endpoint
    # (e.g. a local Ollama for small fast models) via role_model_api_base.
    # The provider's API key is never forwarded to that endpoint.
    role_api_base = settings.role_model_api_base or primary.api_base
    role_api_key = "" if settings.role_model_api_base else primary.api_key
    role_client = LLMClient(
        settings,
        provider=primary.provider,
        model=override,
        api_key=role_api_key,
        api_base=role_api_base,
    )
    # Share the usage accumulator so per-request metering (usage_totals delta
    # on the base client) covers every role's tokens.
    role_client.usage_totals = primary.usage_totals
    logger.debug("role routing: %s -> %s (provider %s)", role, role_client.model, primary.provider)

    if isinstance(base_llm, FailoverLLMClient):
        return FailoverLLMClient([role_client, *base_llm.clients[1:]])
    return role_client
