import asyncio
from types import SimpleNamespace

from backend.agents.output_guardrail import OutputGuardrailAgent
from backend.core.config import Settings
from backend.core.models import (
    Claim,
    EvidenceChunk,
    FinalAnswer,
    SupportLevel,
)


def _settings(tmp_path, **overrides):
    return Settings(llm_provider="mock", data_dir=tmp_path, guardrails_enabled=False, **overrides)


def _state(answer, evidence):
    return {
        "query": "question",
        "route": "retrieval",
        "ranked_evidence": evidence,
        "final_answer": answer,
        "trace": [],
    }


def test_low_confidence_fallback_preserves_human_review(tmp_path):
    settings = _settings(tmp_path)
    evidence = [EvidenceChunk(chunk_id="c1", content="preuve")]
    answer = FinalAnswer(
        answer="Réponse incertaine",
        evidence=evidence,
        confidence=0.0,
        requires_human_review=True,
    )
    state = _state(answer, evidence)

    result = asyncio.run(OutputGuardrailAgent().run(state, SimpleNamespace(settings=settings)))
    guarded = result["final_answer"]

    assert guarded.metadata["low_confidence_fallback"] is True
    assert guarded.requires_human_review is True


def _mixed_answer(contradiction_text: str) -> FinalAnswer:
    good = "Le préavis est de un mois en cas de licenciement selon le Code du travail [1]."
    return FinalAnswer(
        answer=f"{good} {contradiction_text}",
        evidence=[EvidenceChunk(chunk_id="c1", content="preuve")],
        confidence=0.40,
        requires_human_review=True,
        language="fr",
        claims=[
            Claim(claim_id="claim-01", text=good, support_level=SupportLevel.DIRECT),
            Claim(claim_id="claim-02", text=contradiction_text, support_level=SupportLevel.CONTRADICTORY),
        ],
    )


def test_persistent_contradiction_is_salvaged_not_replaced(tmp_path):
    settings = _settings(tmp_path)
    contradiction = "Le délai est de 30 jours pour agir [1]."
    answer = _mixed_answer(contradiction)

    result = asyncio.run(
        OutputGuardrailAgent().run(_state(answer, answer.evidence), SimpleNamespace(settings=settings))
    )
    guarded = result["final_answer"]

    assert "low_confidence_fallback" not in guarded.metadata
    assert guarded.metadata["salvaged_contradiction"] is True
    assert contradiction not in guarded.answer
    assert "préavis" in guarded.answer
    assert "retiré" in guarded.answer  # salvaged note appended
    expected = min(
        max(0.40, settings.human_review_threshold),
        settings.confidence_threshold - 0.01,
    )
    assert guarded.confidence == expected
    assert guarded.confidence < settings.confidence_threshold
    assert guarded.requires_human_review is True


def test_salvage_disabled_falls_back(tmp_path):
    settings = _settings(tmp_path, salvage_contradicted_claims_enabled=False)
    answer = _mixed_answer("Le délai est de 30 jours pour agir [1].")

    result = asyncio.run(
        OutputGuardrailAgent().run(_state(answer, answer.evidence), SimpleNamespace(settings=settings))
    )
    guarded = result["final_answer"]

    assert guarded.metadata["low_confidence_fallback"] is True


def test_fully_contradicted_answer_keeps_fallback(tmp_path):
    settings = _settings(tmp_path)
    contradiction = "Le délai est de 30 jours pour agir [1]."
    answer = FinalAnswer(
        answer=contradiction,
        evidence=[EvidenceChunk(chunk_id="c1", content="preuve")],
        confidence=0.40,
        requires_human_review=True,
        language="fr",
        claims=[Claim(claim_id="claim-01", text=contradiction, support_level=SupportLevel.CONTRADICTORY)],
    )

    result = asyncio.run(
        OutputGuardrailAgent().run(_state(answer, answer.evidence), SimpleNamespace(settings=settings))
    )
    guarded = result["final_answer"]

    assert guarded.metadata["low_confidence_fallback"] is True
