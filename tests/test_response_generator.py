"""Tests for the response generator agent.

Covers the LLM-only answer policy (with one corrective retry for missing
citations), honest unavailability/insufficiency messages, extraction-artifact
repair in excerpts, and the coverage-aware confidence score.
"""

from types import SimpleNamespace

import pytest

from backend.agents.response_generator import ResponseGeneratorAgent
from backend.core.models import EvidenceChunk


class StubLLM:
    """Scripted LLM: returns queued outputs, then empty strings."""

    def __init__(self, outputs: list[str]):
        self.outputs = list(outputs)
        self.calls = 0
        self.systems: list[str] = []

    async def complete(self, system: str, user: str, temperature=None) -> str:
        self.calls += 1
        self.systems.append(system)
        return self.outputs.pop(0) if self.outputs else ""


LONG_ARTICLE = (
    "Article 542 — Le divorce peut être prononcé pour rupture de la vie commune, "
    "pour atteinte grave aux devoirs du mariage ou d'un commun accord des époux. "
    "La rupture de la vie commune est constituée par la cessation de la cohabitation "
    "pendant une durée d'au moins deux ans. " + "Le juge vérifie les faits. " * 20
)


def _evidence(content: str = LONG_ARTICLE) -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            document_name="Code des personnes et de la famille",
            article="542",
            content=content,
        )
    ]


def _state(evidence: list[EvidenceChunk]) -> dict:
    return {
        "query": "Quelle est la procédure de divorce selon le Code des personnes et de la famille ?",
        "ranked_evidence": evidence,
        "language": "fr",
        "plan": None,
        "trace": [],
        "errors": [],
        "conflicts": [],
    }


def _ctx(settings, llm: StubLLM):
    return SimpleNamespace(llm=llm, settings=settings)


@pytest.mark.asyncio
async def test_llm_answer_used_directly_when_cited(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Le divorce peut être prononcé pour trois causes [1]."])
    result = await agent.run(_state(_evidence()), _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.answer == "Le divorce peut être prononcé pour trois causes [1]."
    assert llm.calls == 1
    assert any(c.verified for c in answer.citations)


@pytest.mark.asyncio
async def test_retry_once_when_llm_omits_citations(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM([
        "Le divorce est possible.",  # no [n] citation -> corrective retry
        "Le divorce peut être prononcé pour rupture de la vie commune [1].",
    ])
    result = await agent.run(_state(_evidence()), _ctx(settings, llm))
    answer = result["final_answer"]
    assert llm.calls == 2
    assert "[1]" in answer.answer
    assert "rupture de la vie commune" in answer.answer


@pytest.mark.asyncio
async def test_llm_failure_returns_honest_unavailability(settings):
    """LLM-only policy: no pre-written article list is ever substituted."""
    agent = ResponseGeneratorAgent()
    llm = StubLLM([])  # always empty -> every provider failed
    result = await agent.run(_state(_evidence()), _ctx(settings, llm))
    answer = result["final_answer"]
    assert "momentanément" in answer.answer
    # No fabricated content, no fake citations; the sources stay attached.
    assert not answer.citations
    assert answer.evidence
    assert "dispose que" not in answer.answer


@pytest.mark.asyncio
async def test_no_evidence_returns_insufficient_message(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM([])
    result = await agent.run(_state([]), _ctx(settings, llm))
    answer = result["final_answer"]
    assert "insuffisantes" in answer.answer
    assert answer.confidence == 0.0
    assert any("Aucune preuve" in w for w in answer.warnings)


@pytest.mark.asyncio
async def test_evidence_formatting_repairs_hyphenation_for_llm(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse fondée [1]."])
    content = "Le libre consente - ment est requis."
    await agent.run(_state(_evidence(content)), _ctx(settings, llm))
    # The excerpt shown to the LLM was cleaned before being sent.
    assert "consentement" in agent._build_user_message(_state(_evidence(content)))


def _state_with_plan(sub_questions: list[str], evidence: list[EvidenceChunk]) -> dict:
    from backend.core.models import RetrievalPlan

    state = _state(evidence)
    state["plan"] = RetrievalPlan(
        sub_questions=sub_questions,
        tasks=[],
        legal_domains=[],
        retrieval_language="fr",
        response_language="fr",
        scenario_date=None,
        rationale="test",
    )
    return state


@pytest.mark.asyncio
async def test_confidence_drops_when_subquestions_uncovered(settings):
    """A fully-cited but partial answer must not display 100% confidence."""
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse partielle [1]."])
    evidence = _evidence()  # only covers divorce grounds
    state = _state_with_plan(
        [
            "causes du divorce",  # covered by the evidence
            "partage des biens après le divorce",  # NOT covered
            "garde des enfants et pension alimentaire",  # NOT covered
        ],
        evidence,
    )
    result = await agent.run(state, _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.confidence < 1.0
    assert any("incomplète" in w for w in answer.warnings)


@pytest.mark.asyncio
async def test_full_coverage_keeps_high_confidence(settings):
    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state_with_plan(["rupture de la vie commune et divorce"], _evidence())
    result = await agent.run(state, _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.confidence == 1.0
    assert not any("incomplète" in w for w in answer.warnings)


@pytest.mark.asyncio
async def test_unresolved_conflict_caps_confidence(settings):
    from backend.core.models import ConflictReport

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(_evidence())
    state["conflicts"] = [
        ConflictReport(
            topic="Code du travail art. 70",
            kept_chunk_id="a",
            dropped_chunk_id="b",
            reason="conflit non résolu",
            resolved=False,
        )
    ]
    result = await agent.run(state, _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.confidence <= 0.6
    assert any("contredisent" in w for w in answer.warnings)


@pytest.mark.asyncio
async def test_reflection_gap_caps_confidence(settings):
    from backend.core.models import ReflectionResult

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(_evidence())
    state["reflection"] = ReflectionResult(
        complete=False,
        answered_all_questions=False,
        all_claims_cited=True,
        contradictions_found=False,
        issues=["partie non couverte"],
        should_retry_retrieval=False,
        retry_query=None,
    )
    result = await agent.run(state, _ctx(settings, llm))
    answer = result["final_answer"]
    assert answer.confidence <= 0.75
    assert any("auto-évaluation" in w for w in answer.warnings)


def test_prompt_contains_fewshot_examples():
    assert "FEW-SHOT EXAMPLES" in ResponseGeneratorAgent.system_prompt
    assert "Réponse:" in ResponseGeneratorAgent.system_prompt


def test_prompt_requires_data_separation_and_prose_source_naming():
    """Spec §41/§42: excerpts are DATA, sources named in prose at first citation."""
    prompt = ResponseGeneratorAgent.system_prompt
    assert "DATA" in prompt
    assert "Selon l'article" in prompt
    assert "evidence metadata" in prompt


@pytest.mark.asyncio
async def test_sectioned_structure_prompt_for_complex_question_types(settings):
    """Spec §40: RIGHTS/OBLIGATIONS/PROCEDURE/... get the sectioned addendum."""
    from backend.core.models import QuestionType, RetrievalPlan

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(_evidence())
    state["plan"] = RetrievalPlan(question_type=QuestionType.PROCEDURE)
    await agent.run(state, _ctx(settings, llm))
    assert "## Fondements juridiques" in llm.systems[0]
    assert "## Points d'incertitude" in llm.systems[0]


@pytest.mark.asyncio
async def test_sectioned_structure_prompt_english_for_complex_questions(settings):
    from backend.core.models import QuestionType, RetrievalPlan

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Complete answer [1]."])
    state = _state(_evidence())
    state["language"] = "en"
    state["plan"] = RetrievalPlan(question_type=QuestionType.RIGHTS)
    await agent.run(state, _ctx(settings, llm))
    assert "## Legal basis" in llm.systems[0]
    assert "## Fondements juridiques" not in llm.systems[0]


@pytest.mark.asyncio
async def test_simple_question_types_keep_unsectioned_prompt(settings):
    from backend.core.models import QuestionType, RetrievalPlan

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(_evidence())
    state["plan"] = RetrievalPlan(question_type=QuestionType.FACTUAL)
    await agent.run(state, _ctx(settings, llm))
    assert "## Fondements juridiques" not in llm.systems[0]


@pytest.mark.asyncio
async def test_case_analysis_prompt_has_structure_and_labels_fr(settings):
    """Spec §31: CASE_ANALYSIS gets its own structure plus per-statement labels."""
    from backend.core.models import QuestionType, RetrievalPlan

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(_evidence())
    state["plan"] = RetrievalPlan(question_type=QuestionType.CASE_ANALYSIS)
    await agent.run(state, _ctx(settings, llm))
    prompt = llm.systems[0]
    # Dedicated case-analysis structure instead of the generic §40 sections.
    for section in (
        "## Faits",
        "## Qualification juridique",
        "## Règles applicables",
        "## Application",
        "## Incertitudes",
    ):
        assert section in prompt
    assert "## Fondements juridiques" not in prompt
    # Per-statement labeling contract.
    assert "[LOI]" in prompt
    assert "[APPLICATION]" in prompt
    assert "[HYPOTHÈSE]" in prompt
    # The [n] citation contract is kept for statutory statements.
    assert "[n]" in prompt


@pytest.mark.asyncio
async def test_case_analysis_prompt_english_labels(settings):
    from backend.core.models import QuestionType, RetrievalPlan

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Complete answer [1]."])
    state = _state(_evidence())
    state["language"] = "en"
    state["plan"] = RetrievalPlan(question_type=QuestionType.CASE_ANALYSIS)
    await agent.run(state, _ctx(settings, llm))
    prompt = llm.systems[0]
    assert "## Facts" in prompt
    assert "## Applicable rules" in prompt
    assert "[LAW]" in prompt
    assert "[APPLICATION]" in prompt
    assert "[ASSUMPTION]" in prompt
    assert "[HYPOTHÈSE]" not in prompt


@pytest.mark.asyncio
async def test_case_analysis_labels_absent_for_other_question_types(settings):
    """Labels are CASE_ANALYSIS-only: other types keep the §40 sections."""
    from backend.core.models import QuestionType, RetrievalPlan

    agent = ResponseGeneratorAgent()
    llm = StubLLM(["Réponse complète [1]."])
    state = _state(_evidence())
    state["plan"] = RetrievalPlan(question_type=QuestionType.PROCEDURE)
    await agent.run(state, _ctx(settings, llm))
    prompt = llm.systems[0]
    assert "## Fondements juridiques" in prompt
    assert "[LOI]" not in prompt
    assert "[HYPOTHÈSE]" not in prompt
    assert "## Faits" not in prompt
