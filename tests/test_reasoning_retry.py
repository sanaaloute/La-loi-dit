"""Reasoning-agent retrieval retry gating (latency guard).

Production trace: the reasoning agent requested a full retrieval retry with
18 ranked chunks and 89% coverage — every branch re-ran, doubling retrieval
latency, for zero new evidence. The retry is now honored only when evidence
is genuinely thin or coverage is low.
"""

from types import SimpleNamespace

from backend.agents.reasoning_agent import ReasoningAgent
from backend.core.config import Settings
from backend.core.models import CoverageReport, EvidenceChunk, RetrievalPlan


def _settings(tmp_path):
    return Settings(llm_provider="mock", data_dir=tmp_path, guardrails_enabled=False)


def _state(chunks, coverage=1.0):
    return {
        "query": "délai de recours",
        "plan": RetrievalPlan(sub_questions=["délai de recours"], tasks=[]),
        "ranked_evidence": chunks,
        "coverage_report": CoverageReport(coverage=coverage),
        "retrieval_retries": 0,
        "needs_more_retrieval": False,
        "errors": [],
        "trace": [],
    }


def _chunks(n):
    return [EvidenceChunk(chunk_id=f"c{i}", content=f"contenu {i}") for i in range(n)]


def test_insufficient_with_rich_evidence_does_not_retry(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    result = ReasoningAgent()._parse_final("INSUFFICIENT: il manque des éléments", _state(_chunks(18)), ctx)
    assert result["needs_more_retrieval"] is False
    assert "retry declined" in result["trace"][-1]


def test_insufficient_with_thin_evidence_retries(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    result = ReasoningAgent()._parse_final("INSUFFICIENT: presque rien", _state(_chunks(1)), ctx)
    assert result["needs_more_retrieval"] is True


def test_insufficient_with_low_coverage_retries(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    result = ReasoningAgent()._parse_final("INSUFFICIENT: couverture partielle", _state(_chunks(10), coverage=0.3), ctx)
    assert result["needs_more_retrieval"] is True


def test_normal_analysis_never_retries(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    result = ReasoningAgent()._parse_final("L'article 89 dispose que…", _state(_chunks(1)), ctx)
    assert result["needs_more_retrieval"] is False


# --- reflection agent: same sufficiency gate ---------------------------------

import json as _json

from backend.agents.reflection_agent import ReflectionAgent


def _reflection_state(chunks, coverage=1.0):
    state = _state(chunks, coverage)
    state["reasoning_notes"] = "analyse"
    state["reflection_count"] = 0
    return state


def test_reflection_retry_declined_with_rich_evidence(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    llm_json = _json.dumps({"should_retry_retrieval": True, "answered_all_questions": False})
    result = ReflectionAgent()._parse_final(llm_json, _reflection_state(_chunks(18)), ctx)
    assert result["reflection"].should_retry_retrieval is False
    assert "retry declined" in result["trace"][-1]


def test_reflection_retry_honored_with_thin_evidence(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    llm_json = _json.dumps({"should_retry_retrieval": True, "answered_all_questions": False})
    result = ReflectionAgent()._parse_final(llm_json, _reflection_state(_chunks(1)), ctx)
    assert result["reflection"].should_retry_retrieval is True


def test_reflection_retry_honored_with_low_coverage(tmp_path):
    ctx = SimpleNamespace(settings=_settings(tmp_path))
    llm_json = _json.dumps({"should_retry_retrieval": True, "answered_all_questions": False})
    result = ReflectionAgent()._parse_final(llm_json, _reflection_state(_chunks(10), coverage=0.3), ctx)
    assert result["reflection"].should_retry_retrieval is True
