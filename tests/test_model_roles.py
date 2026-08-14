"""Per-node-role model routing (spec §46): cheap models for simple nodes,
the strong model reserved for final synthesis.

Fully offline: no test performs a real LLM call — role clients are only
constructed and inspected, and graph wiring tests stub every node.
"""

from __future__ import annotations

from backend.core.config import Settings
from backend.core.llm import FailoverLLMClient, LLMClient
from backend.core.model_roles import resolve_role_llm, role_model_override


def _online_settings(**overrides) -> Settings:
    """Non-mock settings so model names actually bind (no calls are made)."""
    return Settings(llm_provider="openai", llm_model="gpt-4o", **overrides)


# ---------------------------------------------------------------------------
# resolve_role_llm unit tests
# ---------------------------------------------------------------------------


def test_disabled_returns_base_client_identity():
    settings = _online_settings(synthesis_model="gpt-4o-strong")
    base = LLMClient(settings)
    for role in ("planner", "classification", "analysis", "synthesis"):
        assert resolve_role_llm(role, base, settings) is base


def test_enabled_binds_role_model():
    settings = _online_settings(
        model_role_routing_enabled=True,
        planner_model="gpt-4o-mini",
        classification_model="gpt-4o-mini",
        analysis_model="gpt-4o",
        synthesis_model="o1",
    )
    base = LLMClient(settings)
    expected = {
        "planner": "gpt-4o-mini",
        "classification": "gpt-4o-mini",
        "analysis": "gpt-4o",
        "synthesis": "o1",
    }
    for role, model in expected.items():
        client = resolve_role_llm(role, base, settings)
        assert isinstance(client, LLMClient)
        assert client.model == model
        assert client.provider == base.provider


def test_missing_override_falls_back_to_base():
    settings = _online_settings(model_role_routing_enabled=True, synthesis_model="o1")
    base = LLMClient(settings)
    assert resolve_role_llm("planner", base, settings) is base
    assert resolve_role_llm("classification", base, settings) is base
    assert resolve_role_llm("analysis", base, settings) is base
    assert resolve_role_llm("unknown-role", base, settings) is base
    assert resolve_role_llm("synthesis", base, settings).model == "o1"


def test_mock_provider_is_a_noop():
    settings = Settings(
        llm_provider="mock",
        model_role_routing_enabled=True,
        planner_model="gpt-4o-mini",
        synthesis_model="o1",
    )
    base = LLMClient(settings)
    for role in ("planner", "classification", "analysis", "synthesis"):
        assert resolve_role_llm(role, base, settings) is base


def test_failover_wrapper_keeps_chain_and_swaps_primary():
    settings = _online_settings(model_role_routing_enabled=True, analysis_model="o1")
    primary = LLMClient(settings)
    fallback = LLMClient(settings, model="gpt-4o-mini")
    base = FailoverLLMClient([primary, fallback])
    resolved = resolve_role_llm("analysis", base, settings)
    assert isinstance(resolved, FailoverLLMClient)
    assert resolved.primary.model == "o1"
    assert resolved.clients[1] is fallback  # fallback chain preserved
    # Role without override: the whole chain is returned untouched.
    assert resolve_role_llm("planner", base, settings) is base


def test_role_clients_share_the_usage_accumulator():
    settings = _online_settings(model_role_routing_enabled=True, synthesis_model="o1")
    base = LLMClient(settings)
    client = resolve_role_llm("synthesis", base, settings)
    assert client.usage_totals is base.usage_totals
    client.usage_totals["tokens_in"] += 7
    assert base.usage_totals["tokens_in"] == 7


def test_failover_role_client_meters_into_base_chain():
    settings = _online_settings(model_role_routing_enabled=True, synthesis_model="o1")
    base = FailoverLLMClient([LLMClient(settings), LLMClient(settings, model="gpt-4o-mini")])
    resolved = resolve_role_llm("synthesis", base, settings)
    resolved.primary.usage_totals["tokens_out"] += 5
    assert base.usage_totals["tokens_out"] == 5


def test_role_model_override_reads_settings():
    settings = _online_settings(planner_model="gpt-4o-mini")
    assert role_model_override("planner", settings) == "gpt-4o-mini"
    assert role_model_override("synthesis", settings) is None
    assert role_model_override("nope", settings) is None


# ---------------------------------------------------------------------------
# Graph wiring: which client each node actually receives
# ---------------------------------------------------------------------------

# node name -> (module under backend.agents, node-function attribute)
_AGENT_NODES = {
    "input_guardrail": ("input_guardrail", "input_guardrail_node"),
    "refusal": ("refusal", "refusal_node"),
    "query_router": ("query_router", "query_router_node"),
    "context_agent": ("context_agent", "context_agent_node"),
    "memory_agent": ("memory_agent", "memory_agent_node"),
    "retrieval_branch": ("retrieval_node", "retrieval_branch_node"),
    "retrieval_merge": ("retrieval_node", "retrieval_merge_node"),
    "conflict_resolver": ("conflict_resolver", "conflict_resolver_node"),
    "evidence_ranking": ("evidence_ranking", "evidence_ranking_node"),
    "parent_expansion": ("parent_expansion", "parent_expansion_node"),
    "coverage_auditor": ("coverage_auditor", "coverage_auditor_node"),
    "reasoning_agent": ("reasoning_agent", "reasoning_agent_node"),
    "reflection_agent": ("reflection_agent", "reflection_agent_node"),
    "citation_verification": ("citation_verification", "citation_verification_node"),
    "claim_verification": ("claim_verification", "claim_verification_node"),
    "response_generator": ("response_generator", "response_generator_node"),
    "output_guardrail": ("output_guardrail", "output_guardrail_node"),
}


def _stub_all_nodes(monkeypatch):
    """Replace every graph node with a recorder; returns {node_name: model}."""
    import backend.agents as agents_pkg
    import backend.workflows.graph as graph_module

    seen: dict[str, str] = {}

    def recorder(name):
        async def stub(state, ctx):
            seen[name] = ctx.llm.model
            return {}

        return stub

    for node_name, (module_name, attr) in _AGENT_NODES.items():
        monkeypatch.setattr(getattr(agents_pkg, module_name), attr, recorder(node_name))
    monkeypatch.setattr(graph_module, "planner_node", recorder("planner"))
    return seen


async def _run_stubbed_graph(monkeypatch, settings: Settings) -> dict[str, str]:
    from backend.core.context import AppContext
    from backend.workflows.graph import build_graph, initial_state

    seen = _stub_all_nodes(monkeypatch)
    ctx = AppContext(settings=settings, llm=LLMClient(settings), cache=None, embedder=None)
    graph = build_graph(ctx)
    await graph.ainvoke(initial_state("question de test"))
    return seen


async def test_graph_wiring_binds_role_models(monkeypatch):
    settings = _online_settings(
        model_role_routing_enabled=True,
        planner_model="cheap-planner",
        classification_model="cheap-classify",
        analysis_model="mid-analysis",
        synthesis_model="strong-synthesis",
    )
    seen = await _run_stubbed_graph(monkeypatch, settings)
    assert seen["planner"] == "cheap-planner"
    assert seen["query_router"] == "cheap-classify"
    assert seen["context_agent"] == "cheap-classify"
    assert seen["memory_agent"] == "cheap-classify"
    assert seen["reasoning_agent"] == "mid-analysis"
    assert seen["reflection_agent"] == "mid-analysis"
    assert seen["response_generator"] == "strong-synthesis"
    # Unroled nodes keep the request's base model.
    for node in ("retrieval_branch", "conflict_resolver", "coverage_auditor", "output_guardrail"):
        assert seen[node] == "gpt-4o"


async def test_graph_wiring_disabled_keeps_base_everywhere(monkeypatch):
    settings = _online_settings(synthesis_model="strong-synthesis")  # routing OFF
    seen = await _run_stubbed_graph(monkeypatch, settings)
    assert seen  # the pipeline actually ran
    assert set(seen.values()) == {"gpt-4o"}


async def test_graph_wiring_partial_overrides_fall_back(monkeypatch):
    settings = _online_settings(model_role_routing_enabled=True, synthesis_model="strong-synthesis")
    seen = await _run_stubbed_graph(monkeypatch, settings)
    assert seen["response_generator"] == "strong-synthesis"
    assert seen["planner"] == "gpt-4o"
    assert seen["reasoning_agent"] == "gpt-4o"


async def test_graph_e2e_mock_with_role_routing_enabled(seeded_ctx):
    """Mock provider + role routing ON must behave exactly like today."""
    from backend.workflows.graph import build_graph, initial_state, run_query

    seeded_ctx.settings.model_role_routing_enabled = True
    seeded_ctx.settings.planner_model = "cheap-planner"
    seeded_ctx.settings.synthesis_model = "strong-synthesis"
    graph = build_graph(seeded_ctx)
    response = await run_query(graph, seeded_ctx, initial_state("Quels sont les droits du locataire ?"))
    assert response.answer is not None
