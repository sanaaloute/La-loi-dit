from backend.core.config import Settings
from backend.core.llm import ROLE_MODEL_CONFIGS, get_llm
from backend.core.model_roles import resolve_role_llm


def test_role_clients_have_independent_generation_defaults(tmp_path):
    settings = Settings(llm_provider="afrikia", data_dir=tmp_path, llm_api_base="https://afrikia.neobytech.net/v1")
    base = get_llm(settings)

    structured = resolve_role_llm("classification", base, settings)
    synthesis = resolve_role_llm("synthesis", base, settings)
    planner = resolve_role_llm("planner", base, settings)

    assert structured.model == "openai/qwen2.5-7b"
    assert structured._generation_kwargs(None, None)["max_tokens"] == 2048
    assert "reasoning_effort" not in structured._generation_kwargs(None, None)
    assert synthesis is base
    assert synthesis.model == "openai/nemotron-3-nano"
    assert synthesis._generation_kwargs(None, None)["reasoning_effort"] == "low"
    assert synthesis._generation_kwargs(None, None)["temperature"] == 0.2
    assert planner.model == "openai/qwen2.5-7b"
    assert "reasoning_effort" not in planner._generation_kwargs(None, None)
    analysis = resolve_role_llm("analysis", base, settings)
    assert analysis.timeout_seconds == 120.0
    assert analysis._generation_kwargs(None, None)["max_tokens"] == 2048
    assert analysis._generation_kwargs(None, None)["reasoning_effort"] == "low"
    assert structured.usage_totals is base.usage_totals

    override = structured._generation_kwargs(0.2, 99)
    assert override["temperature"] == 0.2
    assert override["max_tokens"] == 99
    assert ROLE_MODEL_CONFIGS["classification"]["model"] == "qwen2.5-7b"
    assert ROLE_MODEL_CONFIGS["planner"]["model"] == "qwen2.5-7b"
