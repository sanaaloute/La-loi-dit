"""Claim verification tests (pure heuristics, offline)."""

from __future__ import annotations

from backend.agents.claim_verification import (
    claim_verification_node,
    classify_support,
    extract_claims,
    verify_claims,
)
from backend.core.models import (
    Claim,
    ConfidenceBreakdown,
    EvidenceChunk,
    FinalAnswer,
    SupportLevel,
)


def _evidence() -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            document_name="Code du travail",
            article="95",
            content="Le préavis est d'un mois pour les employés mensualisés.",
        ),
        EvidenceChunk(
            document_name="Code général des impôts",
            article="271",
            content="Le taux de la TVA est de 18%.",
        ),
    ]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_extract_claims_skips_headings_sources_and_short_sentences():
    answer = (
        "## Réponse\n"
        "Le préavis est d'un mois pour les employés mensualisés [1].\n"
        "Oui.\n"
        "## Sources\n"
        "[1] Code du travail, article 95 : le préavis est d'un mois pour les employés.\n"
        "## Notes\n"
        "La loi de finances fixe le taux applicable chaque année.\n"
    )
    claims = extract_claims(answer)
    assert claims == [
        "Le préavis est d'un mois pour les employés mensualisés [1].",
        "La loi de finances fixe le taux applicable chaque année.",
    ]


def test_extract_claims_keeps_keyword_sentence_without_marker():
    answer = "The law provides that notice is one month for monthly-paid employees."
    assert extract_claims(answer) == [answer]


def test_extract_claims_ignores_bare_citation_lines():
    assert extract_claims("[1] [2]\n- Code du travail, art. 95") == []


# ---------------------------------------------------------------------------
# Support classification
# ---------------------------------------------------------------------------


def test_classify_direct_with_matching_numbers():
    chunk = _evidence()[1]
    assert (
        classify_support("Le taux de la TVA est de 18% selon le code [2].", chunk)
        is SupportLevel.DIRECT
    )


def test_classify_contradictory_on_number_mismatch_same_topic():
    chunk = _evidence()[0]
    # Same legal topic (préavis/employés) but the duration conflicts: 3 != 1 mois.
    assert (
        classify_support("Le préavis est de 3 mois pour les employés mensualisés [1].", chunk)
        is SupportLevel.CONTRADICTORY
    )


def test_classify_indirect_on_partial_topical_overlap():
    chunk = _evidence()[0]
    claim = "Le préavis d'un mois concerne les employés licenciés pour faute grave."
    assert classify_support(claim, chunk) is SupportLevel.INDIRECT


def test_classify_insufficient_on_unrelated_chunk():
    chunk = _evidence()[0]
    assert (
        classify_support("Le taux de la TVA est de 18% en vigueur [1].", chunk)
        is SupportLevel.INSUFFICIENT
    )


def test_classify_never_contradictory_without_strong_topic_overlap():
    chunk = _evidence()[0]
    # Numbers mismatch but the chunk is not on the claim's topic: conservative.
    assert (
        classify_support("La pension alimentaire est de 25000 francs par enfant [1].", chunk)
        is SupportLevel.INSUFFICIENT
    )


# ---------------------------------------------------------------------------
# Claim building
# ---------------------------------------------------------------------------


def test_verify_claims_sources_follow_citation_markers():
    evidence = _evidence()
    answer = (
        "Le préavis est d'un mois pour les employés mensualisés [1]. "
        "Le taux de la TVA est de 18% selon le code [2]."
    )
    claims = verify_claims(answer, evidence)
    assert len(claims) == 2
    assert [s.chunk_id for s in claims[0].sources] == [evidence[0].chunk_id]
    assert [s.chunk_id for s in claims[1].sources] == [evidence[1].chunk_id]
    assert claims[0].sources[0].document_name == "Code du travail"
    assert claims[0].sources[0].article == "95"
    assert all(c.support_level is SupportLevel.DIRECT for c in claims)


def test_verify_claims_without_marker_matches_by_overlap():
    evidence = _evidence()
    answer = "Le préavis concerne les employés mensualisés selon la loi en cas de licenciement."
    claims = verify_claims(answer, evidence)
    assert len(claims) == 1
    assert claims[0].support_level is SupportLevel.INDIRECT
    assert [s.chunk_id for s in claims[0].sources] == [evidence[0].chunk_id]


def test_verify_claims_without_marker_and_no_match_is_insufficient():
    evidence = _evidence()
    answer = "Le droit fiscal ne prévoit aucune règle sur la météo agricole des récoltes."
    claims = verify_claims(answer, evidence)
    assert len(claims) == 1
    assert claims[0].support_level is SupportLevel.INSUFFICIENT
    assert claims[0].sources == []


def test_verify_claims_out_of_range_marker_yields_no_source():
    evidence = _evidence()
    answer = "Le préavis est d'un mois pour les employés mensualisés [9]."
    claims = verify_claims(answer, evidence)
    assert claims[0].sources == []
    assert claims[0].support_level is SupportLevel.INSUFFICIENT


def test_final_answer_claims_default_empty():
    assert FinalAnswer(answer="texte").claims == []


# ---------------------------------------------------------------------------
# Node behavior
# ---------------------------------------------------------------------------


def _state(answer_text: str, *, language: str = "fr") -> dict:
    return {
        "query": "question ?",
        "final_answer": FinalAnswer(
            answer=answer_text,
            language=language,
            confidence_breakdown=ConfidenceBreakdown(),
        ),
        "ranked_evidence": _evidence(),
        "trace": [],
        "errors": [],
    }


async def test_node_attaches_claims_and_full_support_confidence():
    state = _state(
        "Le préavis est d'un mois pour les employés mensualisés [1]. "
        "Le taux de la TVA est de 18% selon le code [2]."
    )
    update = await claim_verification_node(state, ctx=None)
    final = update["final_answer"]
    assert len(final.claims) == 2
    assert all(c.support_level is SupportLevel.DIRECT for c in final.claims)
    assert final.confidence_breakdown.legal_support_confidence == 1.0
    assert final.warnings == []
    assert not final.requires_human_review
    assert update["trace"][-1].startswith("claim_verification: 2 claims")


async def test_node_warns_on_insufficient_claims_bilingual():
    text = "Le droit fiscal ne prévoit aucune règle sur la météo agricole des récoltes [1]."
    update = await claim_verification_node(_state(text), ctx=None)
    final = update["final_answer"]
    assert any("n'ont pas pu être vérifiées" in w for w in final.warnings)
    assert final.confidence_breakdown.legal_support_confidence == 0.0
    assert not final.requires_human_review

    update_en = await claim_verification_node(_state(text, language="en"), ctx=None)
    assert any("could not be verified" in w for w in update_en["final_answer"].warnings)


async def test_node_contradiction_requires_human_review_and_lowers_support():
    state = _state(
        "Le préavis est de 3 mois pour les employés mensualisés [1]. "
        "Le préavis est d'un mois pour les employés mensualisés [1]."
    )
    update = await claim_verification_node(state, ctx=None)
    final = update["final_answer"]
    levels = [c.support_level for c in final.claims]
    assert levels == [SupportLevel.CONTRADICTORY, SupportLevel.DIRECT]
    assert final.requires_human_review is True
    assert any("contredisent" in w for w in final.warnings)
    # supported 1/2 dampened by contradiction share 1/2 -> 0.5 * 0.5
    assert final.confidence_breakdown.legal_support_confidence == 0.25


async def test_node_without_final_answer_only_traces():
    update = await claim_verification_node({"trace": [], "errors": []}, ctx=None)
    assert update["claims"] == []
    assert "final_answer" not in update
    assert update["trace"][-1].startswith("claim_verification: 0 claims")


async def test_node_skips_claim_extraction_when_no_evidence():
    """The insufficient-evidence message must not generate noise warnings.

    Regression: with zero ranked evidence the answer IS the insufficiency
    declaration itself; extracting claims from it (e.g. the sentence pointing
    to a "professionnel du droit") produced a spurious "could not be
    verified" warning on top of the honest refusal.
    """
    state = _state(
        "Je n'ai pas trouvé de preuves vérifiables dans les sources officielles "
        "indexées pour répondre à cette question. Veuillez consulter le Journal "
        "Officiel du Burkina Faso ou un professionnel du droit agréé."
    )
    state["ranked_evidence"] = []
    update = await claim_verification_node(state, ctx=None)
    final = update["final_answer"]
    assert final.claims == []
    assert final.warnings == []
    assert not final.requires_human_review
    assert update["trace"][-1].startswith("claim_verification: 0 claims")


# ---------------------------------------------------------------------------
# LLM entailment refinement (regime-mismatch / deduction detection)
# ---------------------------------------------------------------------------


class _StubLLM:
    """Minimal async LLM stub returning a canned completion."""

    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, temperature: float = 0.0):
        self.calls.append((system, user))
        return self.response


def _refinement_ctx(response: str, *, provider: str = "test-llm", enabled: bool = True):
    from types import SimpleNamespace

    from backend.core.config import Settings

    settings = Settings(
        llm_provider=provider,
        claim_llm_refinement_enabled=enabled,
    )
    return SimpleNamespace(settings=settings, llm=_StubLLM(response))


def _cited_claim(level: SupportLevel, chunk: EvidenceChunk) -> Claim:
    from backend.core.models import ClaimSource

    return Claim(
        claim_id="claim-01",
        text=(
            "Le président du tribunal de grande instance peut autoriser l'époux "
            "réclamant le divorce à passer seul l'acte malgré le refus de son "
            "conjoint [1]."
        ),
        support_level=level,
        sources=[
            ClaimSource(
                chunk_id=chunk.chunk_id,
                document_name=chunk.document_name,
                article=chunk.article,
                support_level=level,
            )
        ],
    )


def _matrimonial_chunk() -> EvidenceChunk:
    # The art. 223-12 trap: topically close to a divorce-refusal question but
    # governing matrimonial-regime acts, not divorce.
    return EvidenceChunk(
        document_name="Code des personnes et de la famille",
        article="223-12",
        section="Régimes matrimoniaux",
        law_number="012-2025/ALT",
        content=(
            "Un époux peut être autorisé par ordonnance du président du "
            "tribunal de grande instance à passer seul un acte pour lequel le "
            "concours ou le consentement de son conjoint serait nécessaire, "
            "si son refus n'est pas justifié par l'intérêt de la famille."
        ),
    )


async def test_refinement_downgrades_regime_mismatch_to_inferred():
    from backend.agents.claim_verification import refine_support_with_llm

    chunk = _matrimonial_chunk()
    claim = _cited_claim(SupportLevel.DIRECT, chunk)
    ctx = _refinement_ctx(
        '{"verdicts": [{"claim_id": "claim-01", "verdict": "inferred", '
        '"reason": "l\'extrait régit les actes du régime matrimonial, pas le divorce"}]}'
    )
    [result] = await refine_support_with_llm([claim], [chunk], ctx)
    assert result.support_level is SupportLevel.INDIRECT
    assert "régime matrimonial" in (result.verification_note or "")
    assert len(ctx.llm.calls) == 1


async def test_refinement_downgrades_to_contradicted():
    from backend.agents.claim_verification import refine_support_with_llm

    chunk = _matrimonial_chunk()
    claim = _cited_claim(SupportLevel.INDIRECT, chunk)
    ctx = _refinement_ctx(
        '{"verdicts": [{"claim_id": "claim-01", "verdict": "contradicted", '
        '"reason": "l\'extrait exclut ce cas"}]}'
    )
    [result] = await refine_support_with_llm([claim], [chunk], ctx)
    assert result.support_level is SupportLevel.CONTRADICTORY


async def test_refinement_keeps_explicit_verdict_untouched():
    from backend.agents.claim_verification import refine_support_with_llm

    chunk = _matrimonial_chunk()
    claim = _cited_claim(SupportLevel.DIRECT, chunk)
    ctx = _refinement_ctx(
        '{"verdicts": [{"claim_id": "claim-01", "verdict": "explicit", "reason": "ok"}]}'
    )
    [result] = await refine_support_with_llm([claim], [chunk], ctx)
    assert result.support_level is SupportLevel.DIRECT
    assert result.verification_note is None


async def test_refinement_never_upgrades_heuristic_flags():
    from backend.agents.claim_verification import refine_support_with_llm

    chunk = _matrimonial_chunk()
    # Heuristic said INSUFFICIENT: the claim is not submitted at all.
    claim = _cited_claim(SupportLevel.INSUFFICIENT, chunk)
    ctx = _refinement_ctx(
        '{"verdicts": [{"claim_id": "claim-01", "verdict": "explicit", "reason": "ok"}]}'
    )
    [result] = await refine_support_with_llm([claim], [chunk], ctx)
    assert result.support_level is SupportLevel.INSUFFICIENT
    assert ctx.llm.calls == []


async def test_refinement_fails_open_on_garbage_response():
    from backend.agents.claim_verification import refine_support_with_llm

    chunk = _matrimonial_chunk()
    claim = _cited_claim(SupportLevel.DIRECT, chunk)
    ctx = _refinement_ctx("ceci n'est pas du JSON")
    [result] = await refine_support_with_llm([claim], [chunk], ctx)
    assert result.support_level is SupportLevel.DIRECT
    assert result.verification_note is None


async def test_refinement_skipped_for_mock_provider_and_when_disabled():
    from backend.agents.claim_verification import refine_support_with_llm

    chunk = _matrimonial_chunk()
    for ctx in (
        _refinement_ctx("{}", provider="mock"),
        _refinement_ctx("{}", enabled=False),
        None,
    ):
        claim = _cited_claim(SupportLevel.DIRECT, chunk)
        [result] = await refine_support_with_llm([claim], [chunk], ctx)
        assert result.support_level is SupportLevel.DIRECT
        if ctx is not None:
            assert ctx.llm.calls == []


async def test_node_flags_inferred_claims_for_user_caution():
    state = _state(
        "Le préavis est d'un mois pour les employés mensualisés [1]. "
        "Le taux de la TVA est de 18% selon le code [2]."
    )
    ctx = _refinement_ctx(
        '{"verdicts": ['
        '{"claim_id": "claim-01", "verdict": "inferred", "reason": "déduction au-delà du texte"},'
        '{"claim_id": "claim-02", "verdict": "explicit", "reason": "ok"}'
        "]}"
    )
    update = await claim_verification_node(state, ctx)
    final = update["final_answer"]
    levels = {c.claim_id: c.support_level for c in final.claims}
    assert levels == {"claim-01": SupportLevel.INDIRECT, "claim-02": SupportLevel.DIRECT}
    assert final.metadata["inferred_claims"], "inferred claims must be flagged for the caution note"
    assert any("déductions" in w for w in final.warnings)
    assert "1 refined by llm" in update["trace"][-1]
    # Both claims remain "supported" (DIRECT/INDIRECT): no support downgrade.
    assert final.confidence_breakdown.legal_support_confidence == 1.0
