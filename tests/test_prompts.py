"""Prompt registry tests (backend.core.prompts).

Covers: built-in defaults identical to what the consumption sites expose,
``prompts_dir`` overrides (``.md``/``.txt``), fallback on missing dir/file,
and the mtime-based override cache refresh.
"""

from __future__ import annotations

import os

import pytest

from backend.core.config import get_settings
from backend.core.prompts import PROMPTS, PromptRef, get_prompt


@pytest.fixture(autouse=True)
def _clean_override_cache():
    from backend.core.prompts import _override_cache

    _override_cache.clear()
    yield
    _override_cache.clear()


def _set_prompts_dir(monkeypatch, value):
    """Point the process-wide settings singleton at a prompts_dir (restored by monkeypatch)."""
    monkeypatch.setattr(get_settings(), "prompts_dir", value)


# ---------------------------------------------------------------------------
# Built-in defaults: registry entries match what every consumption site serves
# ---------------------------------------------------------------------------


def test_builtins_match_agent_system_prompts():
    from backend.agents.reasoning_agent import ReasoningAgent
    from backend.agents.reflection_agent import ReflectionAgent
    from backend.agents.refusal import RefusalAgent
    from backend.agents.response_generator import ResponseGeneratorAgent
    from backend.planner.agent import PlannerAgent

    assert PlannerAgent.system_prompt == PROMPTS["PLANNER_SYSTEM"]
    assert ResponseGeneratorAgent.system_prompt == PROMPTS["RESPONSE_SYSTEM"]
    assert ReasoningAgent.system_prompt == PROMPTS["REASONING_SYSTEM"]
    assert ReflectionAgent.system_prompt == PROMPTS["REFLECTION_SYSTEM"]
    assert RefusalAgent.system_prompt == PROMPTS["REFUSAL_SYSTEM"]
    # Class-level access (no instance) also yields the string.
    assert isinstance(PlannerAgent.system_prompt, str)
    assert "FEW-SHOT EXAMPLES" in ResponseGeneratorAgent.system_prompt


def test_builtins_match_response_generator_messages():
    from backend.agents.response_generator import ResponseGeneratorAgent as RG

    assert RG._insufficient_message("fr") == PROMPTS["RESPONSE_INSUFFICIENT_FR"]
    assert RG._insufficient_message("en") == PROMPTS["RESPONSE_INSUFFICIENT_EN"]
    assert RG._unavailable_message("fr") == PROMPTS["RESPONSE_UNAVAILABLE_FR"]
    assert RG._unavailable_message("en") == PROMPTS["RESPONSE_UNAVAILABLE_EN"]


def test_builtins_match_disclaimers_and_notes():
    from backend.agents import output_guardrail
    from backend.agents.tools import generation

    assert generation._DISCLAIMER_FR == PROMPTS["DISCLAIMER_FR"]
    assert generation._DISCLAIMER_EN == PROMPTS["DISCLAIMER_EN"]
    # output_guardrail consumes the registry directly (backward-compat
    # re-exports of the disclaimers resolve through get_prompt as well).
    assert output_guardrail._DISCLAIMER_FR == PROMPTS["DISCLAIMER_FR"]
    assert output_guardrail._DISCLAIMER_EN == PROMPTS["DISCLAIMER_EN"]
    assert get_prompt("INFO_NOTE_FR") == PROMPTS["INFO_NOTE_FR"]
    assert get_prompt("INFO_NOTE_EN") == PROMPTS["INFO_NOTE_EN"]


def test_get_prompt_returns_builtin_by_default():
    assert get_prompt("PLANNER_SYSTEM") == PROMPTS["PLANNER_SYSTEM"]
    assert get_prompt("RERANK_RESCORE") == PROMPTS["RERANK_RESCORE"]


def test_unknown_prompt_name_raises():
    with pytest.raises(KeyError):
        get_prompt("NO_SUCH_PROMPT")


def test_prompt_ref_rejects_assignment():
    from backend.planner.agent import PlannerAgent

    with pytest.raises(AttributeError):
        PlannerAgent().system_prompt = "hacked"


# ---------------------------------------------------------------------------
# prompts_dir overrides
# ---------------------------------------------------------------------------


def test_override_md_file_changes_planner_prompt(tmp_path, monkeypatch):
    override = "You are a test planner override.\n"
    (tmp_path / "PLANNER_SYSTEM.md").write_text(override, encoding="utf-8")
    _set_prompts_dir(monkeypatch, str(tmp_path))

    from backend.planner.agent import PlannerAgent

    assert get_prompt("PLANNER_SYSTEM") == override
    # The agent resolves the registry at every access, instance and class alike.
    assert PlannerAgent().system_prompt == override
    assert PlannerAgent.system_prompt == override
    # Other prompts are unaffected.
    assert get_prompt("RESPONSE_SYSTEM") == PROMPTS["RESPONSE_SYSTEM"]


def test_override_txt_extension(tmp_path, monkeypatch):
    (tmp_path / "RERANK_RESCORE.txt").write_text("rescore override", encoding="utf-8")
    _set_prompts_dir(monkeypatch, str(tmp_path))
    assert get_prompt("RERANK_RESCORE") == "rescore override"


def test_md_takes_precedence_over_txt(tmp_path, monkeypatch):
    (tmp_path / "DISCLAIMER_FR.md").write_text("md version", encoding="utf-8")
    (tmp_path / "DISCLAIMER_FR.txt").write_text("txt version", encoding="utf-8")
    _set_prompts_dir(monkeypatch, str(tmp_path))
    assert get_prompt("DISCLAIMER_FR") == "md version"


def test_missing_dir_falls_back_to_builtin(tmp_path, monkeypatch):
    _set_prompts_dir(monkeypatch, str(tmp_path / "does-not-exist"))
    assert get_prompt("PLANNER_SYSTEM") == PROMPTS["PLANNER_SYSTEM"]


def test_missing_file_falls_back_to_builtin(tmp_path, monkeypatch):
    (tmp_path / "UNRELATED.md").write_text("x", encoding="utf-8")
    _set_prompts_dir(monkeypatch, str(tmp_path))
    assert get_prompt("REFLECTION_SYSTEM") == PROMPTS["REFLECTION_SYSTEM"]


def test_override_applies_to_consumption_site(tmp_path, monkeypatch):
    """An override file changes what the response generator actually emits."""
    (tmp_path / "RESPONSE_INSUFFICIENT_FR.md").write_text(
        "preuves insuffisantes (override)", encoding="utf-8"
    )
    _set_prompts_dir(monkeypatch, str(tmp_path))

    from backend.agents.response_generator import ResponseGeneratorAgent

    assert ResponseGeneratorAgent._insufficient_message("fr") == "preuves insuffisantes (override)"
    # Untouched language keeps the built-in.
    assert ResponseGeneratorAgent._insufficient_message("en") == PROMPTS["RESPONSE_INSUFFICIENT_EN"]


def test_override_cache_refreshes_on_mtime_change(tmp_path, monkeypatch):
    path = tmp_path / "REFUSAL_FR.md"
    path.write_text("version one", encoding="utf-8")
    first_mtime = path.stat().st_mtime
    _set_prompts_dir(monkeypatch, str(tmp_path))
    assert get_prompt("REFUSAL_FR") == "version one"

    path.write_text("version two", encoding="utf-8")
    # Guarantee a different mtime even on coarse-grained filesystems.
    os.utime(path, (first_mtime + 10, first_mtime + 10))
    assert get_prompt("REFUSAL_FR") == "version two"
