"""Agent-tuning settings: overrides take effect, defaults preserve legacy behavior.

Covers the Settings knobs promoted from hardcoded constants in the planner,
response generator, reasoning/reflection, coverage auditor, claim verification,
conflict resolver and context agent (see ``backend.core.config.Settings``,
"agent behavior knobs" block).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from backend.agents.claim_verification import classify_support, extract_claims
from backend.agents.conflict_resolver import _contradict
from backend.agents.coverage_auditor import question_is_covered
from backend.agents.output_guardrail import _DISCLAIMER_FR as guardrail_disclaimer_fr
from backend.agents.reasoning_agent import ReasoningAgent
from backend.agents.response_generator import ResponseGeneratorAgent, compute_confidence_breakdown
from backend.agents.tools.generation import _DISCLAIMER_FR as generation_disclaimer_fr
from backend.agents.tools.planning import _DOMAIN_KEYWORDS
from backend.core.config import Settings
from backend.core.models import (
    AuthorityLevel,
    ConflictReport,
    CoverageReport,
    EvidenceChunk,
    SearchKind,
    SupportLevel,
)
from backend.planner.agent import _DOMAIN_KEYWORDS as planner_domain_keywords
from backend.planner.agent import PlannerAgent, heuristic_plan
from backend.planner.terminology import expand_terms


def _ctx(settings: Settings):
    """Minimal stand-in for AppContext for the sync parse helpers."""
    return SimpleNamespace(settings=settings)


def _chunk(content: str, **kwargs) -> EvidenceChunk:
    return EvidenceChunk(document_name="Code du travail", article="95", content=content, **kwargs)


# ---------------------------------------------------------------------------
# Defaults preserve the legacy hardcoded values
# ---------------------------------------------------------------------------


def test_agent_tuning_defaults_match_legacy_constants():
    s = Settings()
    assert s.planner_max_tool_iterations == 3
    assert s.planner_max_expansion_tasks == 3
    assert s.planner_max_sub_question_tasks == 6
    assert s.answer_max_excerpt_chars == 4000
    assert s.answer_child_preview_chars == 200
    assert s.confidence_citation_weight == 0.4
    assert s.confidence_coverage_weight == 0.6
    assert s.confidence_unresolved_conflict_dampening == 0.85
    assert s.confidence_reflection_gap_cap == 0.75
    assert s.source_default_authority_weight == 0.15
    assert s.retrieval_top_mean_count == 3
    assert s.temporal_conflict_penalty == 0.5
    assert s.temporal_undated_penalty == 0.6
    assert s.reasoning_max_excerpt_chars == 2000
    assert s.coverage_term_min_length == 4
    assert s.coverage_term_match_ratio == 0.5
    assert s.claim_min_chars == 40
    assert s.claim_direct_term_coverage == 0.7
    assert s.claim_indirect_term_coverage == 0.4
    assert s.claim_contradiction_term_coverage == 0.6
    assert s.conflict_prefix_chars == 80
    assert s.context_buffer_limit == 20
    assert s.context_message_max_chars == 2000


# ---------------------------------------------------------------------------
# Shared constants stay deduplicated (single source of truth)
# ---------------------------------------------------------------------------


def test_disclaimer_text_is_shared_between_guardrail_and_tool():
    assert guardrail_disclaimer_fr is generation_disclaimer_fr


def test_domain_keywords_are_shared_between_planner_and_tool():
    assert planner_domain_keywords is _DOMAIN_KEYWORDS


# ---------------------------------------------------------------------------
# Response generator: confidence blend weights and caps
# ---------------------------------------------------------------------------


def _answer_state(settings: Settings, **overrides):
    state = {
        "query": "Quel est le préavis ?",
        "ranked_evidence": [_chunk("Le préavis est d'un mois pour les employés.")],
        "citation_accuracy": 1.0,
        "coverage_report": CoverageReport(coverage=0.5),
        "conflicts": [],
        "trace": [],
        "errors": [],
    }
    state.update(overrides)
    return state


def test_confidence_blend_weights_change_aggregate():
    agent = ResponseGeneratorAgent()
    default = agent._parse_final("Le préavis est d'un mois [1].", _answer_state(Settings()), _ctx(Settings()))
    tuned = Settings(confidence_citation_weight=1.0, confidence_coverage_weight=0.0)
    overridden = agent._parse_final("Le préavis est d'un mois [1].", _answer_state(tuned), _ctx(tuned))
    # default: 0.4 * 1.0 + 0.6 * 0.5 = 0.7 ; override: 1.0 * 1.0 + 0.0 * 0.5 = 1.0
    assert default["final_answer"].confidence == 0.7
    assert overridden["final_answer"].confidence == 1.0


def test_unresolved_conflict_dampening_is_configurable():
    agent = ResponseGeneratorAgent()
    conflict = ConflictReport(
        topic="Code du travail art. 95",
        kept_chunk_id="a",
        dropped_chunk_id="b",
        reason="conflit non résolu",
        resolved=False,
    )
    tuned = Settings(confidence_unresolved_conflict_dampening=0.5)
    # Base blend: 0.4 * 1.0 + 0.6 * 0.5 = 0.7, dampened once: 0.7 * 0.5 = 0.35.
    update = agent._parse_final(
        "Le préavis est d'un mois [1].",
        _answer_state(tuned, conflicts=[conflict]),
        _ctx(tuned),
    )
    assert update["final_answer"].confidence == 0.35


def test_conflict_dampening_exponent_is_capped():
    """Many unresolved conflicts (parallel undated law versions) decay the
    aggregate to dampening**max_dampenings, never toward zero."""
    agent = ResponseGeneratorAgent()
    conflicts = [
        ConflictReport(
            topic=f"Code du travail art. {90 + i}",
            kept_chunk_id="a",
            dropped_chunk_id="b",
            reason="conflit non résolu",
            resolved=False,
        )
        for i in range(5)
    ]
    tuned = Settings(confidence_unresolved_conflict_dampening=0.5)
    # Base 0.7 x 0.5^3 (capped, not ^5) = 0.0875 -> 0.09.
    update = agent._parse_final(
        "Le préavis est d'un mois [1].",
        _answer_state(tuned, conflicts=conflicts),
        _ctx(tuned),
    )
    assert update["final_answer"].confidence == 0.09


def test_excerpt_and_retrieval_mean_knobs():
    agent = ResponseGeneratorAgent()
    long_chunk = _chunk("a" * 5000)
    default_text = agent._format_evidence([long_chunk], Settings())
    tuned_text = agent._format_evidence([long_chunk], Settings(answer_max_excerpt_chars=100))
    assert "a" * 4000 in default_text
    assert "a" * 101 not in tuned_text and "a" * 100 in tuned_text

    evidence = [
        _chunk("x", retrieval_score=0.9),
        _chunk("y", retrieval_score=0.1),
        _chunk("z", retrieval_score=0.1),
    ]
    state = {"query": "q", "conflicts": []}
    default = compute_confidence_breakdown(state, evidence, citation_accuracy=1.0, coverage=1.0, settings=Settings())
    tuned = compute_confidence_breakdown(
        state, evidence, citation_accuracy=1.0, coverage=1.0,
        settings=Settings(retrieval_top_mean_count=1),
    )
    assert default.retrieval_confidence == 0.37  # mean of top-3: (0.9+0.1+0.1)/3
    assert tuned.retrieval_confidence == 0.9  # top-1 only


# ---------------------------------------------------------------------------
# Claim verification: bars and minimum claim length
# ---------------------------------------------------------------------------


def test_claim_support_bars_change_classification():
    chunk = _chunk("Le taux de la TVA est de 18%.")
    claim = "Le taux de la TVA est de 18% selon le code [2]."
    assert classify_support(claim, chunk, Settings()) is SupportLevel.DIRECT
    tuned = Settings(claim_direct_term_coverage=1.01)  # DIRECT unreachable
    assert classify_support(claim, chunk, tuned) is SupportLevel.INDIRECT


def test_claim_min_chars_changes_extraction():
    sentence = "Le préavis est d'un mois [1]."  # 29 chars: below the default bar
    assert extract_claims(sentence, Settings()) == []
    assert extract_claims(sentence, Settings(claim_min_chars=10)) == [sentence]


# ---------------------------------------------------------------------------
# Coverage auditor: term match ratio
# ---------------------------------------------------------------------------


def test_coverage_term_match_ratio_changes_verdict():
    texts = ["le partage est égal entre les époux"]
    assert question_is_covered("partage des biens", texts, set(), Settings()) is True
    tuned = Settings(coverage_term_match_ratio=1.0)  # every term must appear
    assert question_is_covered("partage des biens", texts, set(), tuned) is False


# ---------------------------------------------------------------------------
# Planner: expansion and sub-question caps
# ---------------------------------------------------------------------------


def _expansion_queries(plan) -> list[str]:
    planned = set(plan.sub_questions)
    return [
        t.query for t in plan.tasks if t.kind == SearchKind.KEYWORD and t.query not in planned
    ]


def test_planner_expansion_cap_changes_task_count():
    query = "Quelles règles pour le licenciement, le divorce, la succession, le bail et l'impôt ?"
    assert len(expand_terms(query)) > 3  # sanity: the cap is what binds
    default = heuristic_plan(query, settings=Settings())
    tuned = heuristic_plan(query, settings=Settings(planner_max_expansion_tasks=1))
    assert len(_expansion_queries(default)) == 3
    assert len(_expansion_queries(tuned)) == 1


def test_planner_sub_question_cap_changes_task_count():
    # The cap binds on LLM-supplied plans: every planned sub-question gets a
    # keyword task, up to planner_max_sub_question_tasks.
    agent = PlannerAgent()
    sub_questions = [f"aspect juridique numéro {i}" for i in range(8)]
    payload = json.dumps({"sub_questions": sub_questions, "tasks": []})
    state = {"query": "question large sur les droits"}

    def keyword_tasks(settings: Settings) -> int:
        update = agent._parse_final(payload, state, _ctx(settings), [])
        return sum(1 for t in update["plan"].tasks if t.kind == SearchKind.KEYWORD)

    capped = keyword_tasks(Settings(planner_max_sub_question_tasks=3))
    default = keyword_tasks(Settings())
    assert capped == 1 + 3  # base keyword task + capped sub-question tasks
    assert default == 1 + 6


# ---------------------------------------------------------------------------
# Reasoning agent: excerpt size and evidence-count linkage
# ---------------------------------------------------------------------------


def test_reasoning_excerpt_chars_and_evidence_count():
    agent = ReasoningAgent()
    chunks = [_chunk(f"contenu-{i} " + "x" * 300) for i in range(12)]
    state = {"query": "q", "ranked_evidence": chunks}

    default_msg = agent._build_user_message(state, _ctx(Settings()))
    assert "x" * 300 in default_msg  # excerpts effectively untruncated at 2000 chars
    # Every ranked chunk reaches the prompt: the evidence count is bounded
    # upstream, per sub-question, by the evidence ranking node.
    assert "contenu-11" in default_msg

    tuned = Settings(reasoning_max_excerpt_chars=20)
    tuned_msg = agent._build_user_message(state, _ctx(tuned))
    assert "x" * 21 not in tuned_msg
    assert "contenu-11" in tuned_msg


# ---------------------------------------------------------------------------
# Conflict resolver: shared-prefix length
# ---------------------------------------------------------------------------


def test_conflict_prefix_chars_changes_contradiction_detection():
    common = "article commun " * 6  # 90 shared leading chars
    a = _chunk(common + "un mois 1", authority=AuthorityLevel.LAW)
    b = _chunk(common + "deux mois 2", authority=AuthorityLevel.LAW)
    # Default: the first 80 chars are identical -> treated as the same text.
    assert _contradict(a, b, Settings()) is False
    # Longer prefix window reaches the diverging part -> contradiction seen.
    assert _contradict(a, b, Settings(conflict_prefix_chars=95)) is True
