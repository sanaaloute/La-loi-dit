from backend.core.config import Settings
from backend.core.llm import ROLE_MODEL_CONFIGS, get_llm
from backend.core.model_roles import resolve_role_llm


def test_role_clients_have_independent_generation_defaults(tmp_path):
    settings = Settings(llm_provider="ollama", data_dir=tmp_path, llm_api_base="http://localhost:11434")
    base = get_llm(settings)

    structured = resolve_role_llm("classification", base, settings)
    synthesis = resolve_role_llm("synthesis", base, settings)

    assert structured.model == "ollama/nemotron-3-nano:4b"
    assert structured.role_options["num_ctx"] == 16384
    assert structured.role_options["think_enabled"] is False
    assert structured._generation_options(None, None)["num_predict"] == 2048
    assert synthesis is base
    assert synthesis.role_options["num_ctx"] == 32768
    assert structured.usage_totals is base.usage_totals

    override = structured._generation_options(0.2, 99)
    assert override["temperature"] == 0.2
    assert override["num_predict"] == 99
    assert override["num_ctx"] == ROLE_MODEL_CONFIGS["classification"]["num_ctx"]
