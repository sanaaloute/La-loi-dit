import asyncio
from types import SimpleNamespace

from backend.agents.output_guardrail import OutputGuardrailAgent
from backend.core.config import Settings
from backend.core.models import EvidenceChunk, FinalAnswer


def test_low_confidence_fallback_preserves_human_review(tmp_path):
    settings = Settings(llm_provider="mock", data_dir=tmp_path, guardrails_enabled=False)
    evidence = [EvidenceChunk(chunk_id="c1", content="preuve")]
    answer = FinalAnswer(
        answer="Réponse incertaine",
        evidence=evidence,
        confidence=0.0,
        requires_human_review=True,
    )
    state = {
        "query": "question",
        "route": "retrieval",
        "ranked_evidence": evidence,
        "final_answer": answer,
        "trace": [],
    }

    result = asyncio.run(OutputGuardrailAgent().run(state, SimpleNamespace(settings=settings)))
    guarded = result["final_answer"]

    assert guarded.metadata["low_confidence_fallback"] is True
    assert guarded.requires_human_review is True
