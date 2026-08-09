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

    async def complete(self, system: str, user: str, temperature=None) -> str:
        self.calls += 1
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
