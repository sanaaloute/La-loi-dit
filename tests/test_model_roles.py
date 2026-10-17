"""Deterministic role-to-model routing tests; no real LLM calls are made."""

from __future__ import annotations

from backend.core.config import Settings
from backend.core.llm import CLASSIFICATION_MODEL, SYNTHESIS_MODEL, LLMClient
from backend.core.model_roles import ROLE_MODELS, resolve_role_llm


def _online_settings(**overrides) -> Settings:
    return Settings(
        llm_provider="ollama",
        llm_model="legacy-model",
        llm_api_base="http://localhost:11434",
        **overrides,
    )


def test_fixed_role_models():
    settings = _online_settings()
    base = LLMClient(settings)
    for role in ("classification", "planner"):
        client = resolve_role_llm(role, base, settings)
        assert client.model == f"ollama/{CLASSIFICATION_MODEL}"
        assert client.provider == "ollama"
    for role in ("analysis", "synthesis"):
        client = resolve_role_llm(role, base, settings)
        assert client.model == f"ollama/{SYNTHESIS_MODEL}"
        assert client.provider == "ollama"
    assert ROLE_MODELS == {
        "classification": CLASSIFICATION_MODEL,
        "planner": CLASSIFICATION_MODEL,
        "analysis": SYNTHESIS_MODEL,
        "synthesis": SYNTHESIS_MODEL,
    }


def test_unknown_role_defaults_to_synthesis():
    settings = _online_settings()
    client = resolve_role_llm("unknown-role", LLMClient(settings), settings)
    assert client.model == f"ollama/{SYNTHESIS_MODEL}"


def test_mock_provider_is_a_noop():
    settings = Settings(llm_provider="mock")
    base = LLMClient(settings)
    for role in ("planner", "classification", "analysis", "synthesis"):
        assert resolve_role_llm(role, base, settings) is base


def test_role_clients_share_the_usage_accumulator_and_drop_external_keys():
    settings = _online_settings(llm_api_key="external-secret")
    base = LLMClient(settings)
    client = resolve_role_llm("classification", base, settings)
    assert client.usage_totals is base.usage_totals
    assert client.api_key == ""
    assert client.api_base == "http://localhost:11434"
    client.usage_totals["tokens_in"] += 7
    assert base.usage_totals["tokens_in"] == 7


def test_same_fixed_model_returns_base_identity():
    settings = _online_settings()
    base = LLMClient(settings, model="gemma4:12b")
    assert resolve_role_llm("synthesis", base, settings) is base


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
    ctx = AppContext(settings=settings, llm=LLMClient(settings, model="legacy-model"), cache=None, embedder=None)
    graph = build_graph(ctx)
    await graph.ainvoke(initial_state("question de test"))
    return seen


async def test_graph_wiring_binds_fixed_role_models(monkeypatch):
    settings = _online_settings()
    seen = await _run_stubbed_graph(monkeypatch, settings)
    for node in ("query_router", "context_agent", "memory_agent", "claim_verification", "planner"):
        assert seen[node] == f"ollama/{CLASSIFICATION_MODEL}"
    for node in ("reasoning_agent", "reflection_agent", "response_generator"):
        assert seen[node] == f"ollama/{SYNTHESIS_MODEL}"
    for node in ("retrieval_branch", "conflict_resolver", "coverage_auditor", "output_guardrail"):
        assert seen[node] == "ollama/legacy-model"


async def test_graph_e2e_mock(seeded_ctx):
    from backend.workflows.graph import build_graph, initial_state, run_query

    graph = build_graph(seeded_ctx)
    response = await run_query(graph, seeded_ctx, initial_state("Quels sont les droits du locataire ?"))
    assert response.answer is not None
