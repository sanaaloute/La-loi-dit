"""Answerability gate: abstention detection, confidence caps, corrective retry.

Regression tests for the production failure where a well-cited non-answer
(« les extraits ne couvrent pas la question ») displayed 96% confidence.
"""

import asyncio
from types import SimpleNamespace

from backend.agents.answerability import (
    confidence_cap,
    detect_abstention,
    has_abstention_language,
)
from backend.agents.claim_verification import ClaimVerificationAgent, extract_claims
from backend.agents.response_generator import ResponseGeneratorAgent
from backend.core.config import Settings
from backend.core.models import EvidenceChunk, RetrievalPlan


def _settings(tmp_path):
    return Settings(llm_provider="mock", data_dir=tmp_path, guardrails_enabled=False)


def _state(chunks, query="délai de prescription"):
    return {
        "query": query,
        "language": "fr",
        "route": "retrieval",
        "plan": RetrievalPlan(sub_questions=[query]),
        "ranked_evidence": chunks,
        "conflicts": [],
        "errors": [],
        "trace": [],
    }


# --- detector ---------------------------------------------------------------


def test_clean_grounded_answer_is_not_flagged(tmp_path):
    text = "Le délai de prescription est de trente ans [1]. Selon l'article 2262 du Code civil [1]."
    report = detect_abstention(text)
    assert report.matched == ()
    assert not report.declares_gap
    assert not report.is_abstention
    assert confidence_cap(text, _settings(tmp_path)) is None


def test_flat_refusal_is_abstention():
    text = (
        "Le délai de recours n'est pas explicitement mentionné dans les sources fournies. "
        "Il est nécessaire de consulter le Code du travail."
    )
    report = detect_abstention(text)
    assert report.declares_gap
    assert report.is_abstention


def test_partial_answer_has_affirmative_content():
    text = (
        "Le préavis est de un mois en cas de licenciement [1]. "
        "Cependant, le délai de recours n'est pas couverte par les extraits fournis."
    )
    report = detect_abstention(text)
    assert report.declares_gap
    assert not report.is_abstention
    assert report.has_affirmative_content


def test_detection_is_accent_and_case_insensitive():
    assert has_abstention_language("Cette information N'EST PAS COUVERTE par les extraits.")
    assert has_abstention_language("Les extraits ne permettent pas de répondre à cette question.")
    assert not has_abstention_language("La prescription est de trente ans [2].")


def test_uncertainty_section_caveat_does_not_flag_structured_answer():
    """The mandated « Points d'incertitude » section must not trigger the gate."""
    text = (
        "## Réponse\nLa saisine de la CCJA se fait par voie de tierce opposition [1].\n"
        "## Fondements juridiques\nSelon l'article 7 du Règlement de la CCJA [1], "
        "la tierce opposition est portée devant la Cour.\n"
        "## Points d'incertitude\nLes délais précis ne sont pas explicitement "
        "mentionnés dans les extraits fournis.\n"
        "## Sources\n1. reglement_ccja.pdf, art. 7"
    )
    report = detect_abstention(text)
    assert report.matched == ()
    assert not report.declares_gap


def test_gap_declared_in_body_still_flagged_with_sections():
    text = (
        "## Réponse\nLe préavis est de un mois [1].\n"
        "## Fondements juridiques\nLe délai de recours n'est pas couverte par les extraits fournis.\n"
        "## Sources\n1. code_travail.pdf"
    )
    report = detect_abstention(text)
    assert report.declares_gap
    assert not report.is_abstention  # body still has an affirmative cited sentence


def test_confidence_cap_levels(tmp_path):
    settings = _settings(tmp_path)
    clean = "Le délai est de trente ans [1]."
    flat = "La réponse n'est pas couverte par les extraits fournis."
    partial = "Le délai est de trente ans [1]. La suite n'est pas couverte par les extraits fournis."

    assert confidence_cap(clean, settings) is None
    assert confidence_cap(flat, settings) == settings.human_review_threshold
    assert confidence_cap(partial, settings) == settings.confidence_threshold - 0.01


# --- extract_claims skips insufficiency declarations -------------------------


def test_extract_claims_skips_abstention_sentences():
    text = (
        "Le préavis est de un mois en cas de licenciement [1]. "
        "Le délai de recours n'est pas couverte par les extraits fournis [1]."
    )
    claims = extract_claims(text)
    assert len(claims) == 1
    assert "préavis" in claims[0]


# --- confidence capping end-to-end (parse + claim verification) --------------


def test_parse_final_caps_well_cited_abstention(tmp_path):
    """The production 96% bug: a cited non-answer must cap at 0.40."""
    settings = _settings(tmp_path)
    ctx = SimpleNamespace(settings=settings)
    chunks = [
        EvidenceChunk(
            chunk_id="c1",
            content="Le délai de prescription des actions personnelles est de trente ans.",
        )
    ]
    state = _state(chunks)
    generated = ResponseGeneratorAgent()._parse_final(
        "Les extraits fournis ne permettent pas de répondre à la question du délai "
        "de prescription [1].",
        state,
        ctx,
    )
    answer = generated["final_answer"]
    assert answer.confidence <= settings.human_review_threshold
    assert answer.metadata["abstention_detected"]
    assert "abstention_partial" not in answer.metadata


def test_claim_verification_keeps_partial_answer_under_threshold(tmp_path):
    settings = _settings(tmp_path)
    ctx = SimpleNamespace(settings=settings, llm=None)
    chunks = [
        EvidenceChunk(
            chunk_id="c1",
            content="Le délai de prescription des actions personnelles est de trente ans.",
        )
    ]
    state = _state(chunks)
    generated = ResponseGeneratorAgent()._parse_final(
        "Le délai de prescription des actions personnelles est de trente ans [1]. "
        "Le point de départ du délai n'est pas couverte par les extraits fournis.",
        state,
        ctx,
    )
    assert generated["final_answer"].metadata.get("abstention_partial") is True

    reviewed = asyncio.run(ClaimVerificationAgent().run(generated, ctx))
    assert reviewed["final_answer"].confidence < settings.confidence_threshold


# --- corrective retry when the evidence covers the question ------------------


def test_abstention_despite_coverage_triggers_corrective_retry(tmp_path):
    calls: list[str] = []

    class FakeLLM:
        provider = "mock"
        model = "fake"

        async def complete(self, system, user, **kwargs):
            calls.append(user)
            if len(calls) == 1:
                return "Cette information n'est pas couverte par les extraits fournis [1]."
            return "Le préavis est de un mois en cas de licenciement [1]."

    settings = _settings(tmp_path)
    chunk = EvidenceChunk(
        chunk_id="c1",
        content="Le préavis en cas de licenciement est de un mois pour les salariés concernés.",
    )
    state = _state([chunk], query="préavis licenciement")
    ctx = SimpleNamespace(settings=settings, llm=FakeLLM())

    generated = asyncio.run(ResponseGeneratorAgent().run(state, ctx))

    assert len(calls) == 2  # initial draft + one abstention corrective
    assert "un mois" in generated["final_answer"].answer
    assert "abstention_detected" not in generated["final_answer"].metadata


def test_persistent_abstention_is_capped_not_regenerated_twice(tmp_path):
    calls: list[str] = []

    class FakeLLM:
        provider = "mock"
        model = "fake"

        async def complete(self, system, user, **kwargs):
            calls.append(user)
            return "La réponse n'est pas couverte par les extraits fournis [1]."

    settings = _settings(tmp_path)
    chunk = EvidenceChunk(
        chunk_id="c1",
        content="Le préavis en cas de licenciement est de un mois pour les salariés concernés.",
    )
    state = _state([chunk], query="préavis licenciement")
    ctx = SimpleNamespace(settings=settings, llm=FakeLLM())

    generated = asyncio.run(ResponseGeneratorAgent().run(state, ctx))

    assert len(calls) == 2  # exactly ONE corrective pass, never a loop
    assert generated["final_answer"].confidence <= settings.human_review_threshold
