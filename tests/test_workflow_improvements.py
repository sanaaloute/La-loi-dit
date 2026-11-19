"""Focused tests for workflow, retrieval, verification, and guardrail improvements."""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.agents.answerability import detect_abstention
from backend.agents.claim_verification import extract_claims
from backend.agents.language_gate import _looks_french
from backend.agents.output_guardrail import output_guardrail_node
from backend.agents.query_router import is_direct_shortcut, is_retrieval_shortcut
from backend.agents.reasoning_agent import _deterministic_calculations
from backend.core.cache import InMemoryCache
from backend.core.config import Settings
from backend.core.context import AppContext
from backend.core.embeddings import HashEmbeddings
from backend.core.llm import LLMClient
from backend.core.models import Citation, EvidenceChunk, FinalAnswer, QuestionType, RetrievalPlan
from backend.retrieval.bm25 import BM25Retriever, tokenize
from backend.workflows.graph import build_graph


def test_query_router_shortcuts_cover_acknowledgements_and_legal_terms():
    assert is_direct_shortcut("ok") is True
    assert is_direct_shortcut("d'accord, merci beaucoup") is True
    assert is_retrieval_shortcut("Quel est le délai de licenciement ?") is True
    assert is_retrieval_shortcut("droits du locataire bail d'habitation") is True
    assert is_retrieval_shortcut("faute lourde contrat de travail") is True


def test_language_gate_recognizes_unaccented_french_legal_queries():
    assert _looks_french("delai de licenciement burkina") is True
    assert _looks_french("What is the notice period for dismissal?") is False


def test_bm25_french_stemming_and_stopwords():
    tokens = tokenize("Les salariés licenciés ont des droits")
    assert "les" not in tokens
    assert "des" not in tokens
    assert "salari" in tokens
    assert "licenci" in tokens
    assert "droit" in tokens

    retriever = BM25Retriever()
    retriever.add_documents(
        [
            EvidenceChunk(
                chunk_id="c1",
                document_name="Code du travail",
                content="Le salarié licencié a droit à un préavis.",
            ),
            EvidenceChunk(
                chunk_id="c2",
                document_name="Code de la route",
                content="Le port du casque est obligatoire pour les motocyclistes.",
            ),
            EvidenceChunk(
                chunk_id="c3",
                document_name="Code civil",
                content="Les conventions légalement formées tiennent lieu de loi.",
            ),
        ]
    )
    hits = retriever.search("licenciement droits salaries", top_k=3)
    assert len(hits) >= 1
    assert hits[0].chunk_id == "c1"


def test_claim_extraction_does_not_split_on_legal_abbreviations():
    text = (
        "Selon l'art. 33 du Code du travail, la durée du préavis est d'un mois "
        "pour les employés mensualisés [1]."
    )
    claims = extract_claims(text)
    assert len(claims) == 1
    assert "[1]" in claims[0]
    assert "art. 33" in claims[0]


def test_answerability_allows_consultation_advice_in_grounded_answer():
    text = (
        "Le salarié licencié a droit à un préavis d'un mois [1]. "
        "Pour votre situation particulière, il convient de consulter un avocat."
    )
    report = detect_abstention(text)
    assert report.declares_gap is False


def test_deterministic_calculations_for_severance_and_notice():
    notes = _deterministic_calculations(
        "Quel est le montant de l'indemnité de licenciement pour un salaire de 200000 FCFA avec 5 ans d'ancienneté pour un cadre ?"
    )
    assert "CALCUL DÉTERMINISTE (indemnité de licenciement)" in notes
    assert "CALCUL DÉTERMINISTE (préavis)" in notes


def test_graph_includes_output_guardrail_and_preserves_cited_moderate_confidence():
    settings = Settings(llm_provider="mock", data_dir=Path("./data/eval"))
    ctx = AppContext(
        settings=settings,
        llm=LLMClient(settings),
        cache=InMemoryCache(60),
        embedder=HashEmbeddings(64),
    )
    graph = build_graph(ctx)
    assert "output_guardrail" in graph.nodes

    chunk = EvidenceChunk(
        chunk_id="c1",
        document_name="Code du travail",
        article="33",
        content="Le préavis est d'un mois pour les employés.",
    )
    answer = FinalAnswer(
        answer="Le préavis est d'un mois pour les employés [1].",
        citations=[
            Citation(
                label="[1]",
                chunk_id="c1",
                document_name="Code du travail",
                article="33",
                verified=True,
            )
        ],
        evidence=[chunk],
        confidence=0.45,
        language="fr",
    )
    state = {
        "query": "Quel est le préavis ?",
        "route": "retrieval",
        "ranked_evidence": [chunk],
        "final_answer": answer,
        "plan": RetrievalPlan(question_type=QuestionType.FACTUAL),
        "trace": [],
    }
    res = asyncio.run(output_guardrail_node(state, ctx))
    out: FinalAnswer = res["final_answer"]
    assert out.metadata.get("low_confidence_fallback") is not True
    assert "Le préavis est d'un mois" in out.answer
    assert "Note :" in out.answer or "Avertissement :" in out.answer
