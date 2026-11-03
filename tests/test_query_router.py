"""Tests for the query router's deterministic shortcuts and LLM fallback."""

import pytest

from backend.agents.query_router import (
    QueryRouterAgent,
    is_direct_shortcut,
    is_retrieval_shortcut,
    parse_route,
)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("bonjour", True),
        ("hi", True),
        ("merci", True),
        ("au revoir", True),
        ("qui es-tu", True),
        ("bonjour, quels sont mes droits ?", False),
        ("Quels sont les délais de prescription en matière civile ?", False),
    ],
)
def test_is_direct_shortcut(query, expected):
    assert is_direct_shortcut(query) is expected


@pytest.mark.parametrize(
    "query,expected",
    [
        ("Quels sont les délais de prescription en matière civile ?", True),
        ("Quel est le délai de prescription des actions civiles au Burkina Faso ?", True),
        ("Quel est le délai de préavis pour un employé mensualisé ?", True),
        ("Quelle peine encourt une personne reconnue coupable de vol ?", True),
        ("Article 35 du Code du travail", True),
        ("bonjour", False),
        ("Qu'est-ce que la démocratie ?", False),
    ],
)
def test_is_retrieval_shortcut(query, expected):
    assert is_retrieval_shortcut(query) is expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("RETRIEVAL", "retrieval"),
        ("GENERAL", "general"),
        ("DIRECT", "direct"),
        ("CLARIFY", "clarify"),
        ("clarify.", "clarify"),
        ("", "retrieval"),
        ("some prose", "retrieval"),
        ("retrieval.", "retrieval"),
    ],
)
def test_parse_route(text, expected):
    assert parse_route(text) == expected


@pytest.mark.asyncio
async def test_clarify_route_returns_deterministic_answer():
    """Unintelligible input gets a canned rephrase request — no LLM, no search."""
    from backend.agents.response_generator import ResponseGeneratorAgent

    agent = ResponseGeneratorAgent()
    state = {"route": "clarify", "query": "Hu", "language": "fr", "trace": [], "errors": []}
    result = await agent.run(state, None)
    answer = result["final_answer"]
    assert answer.metadata["route"] == "clarify"
    assert "préciser" in answer.answer
    assert not answer.evidence


async def _stub_llm_complete(system, user, **kwargs):
    # Pretend the classifier wrongly labels a civil question as GENERAL.
    return "GENERAL"


@pytest.mark.asyncio
async def test_retrieval_shortcut_overrides_misclassifying_llm():
    agent = QueryRouterAgent()
    state = {"query": "Quels sont les délais de prescription en matière civile ?"}
    ctx = type("Ctx", (), {"llm": type("LLM", (), {"complete": _stub_llm_complete})()})()
    result = await agent.run(state, ctx)
    assert result["route"] == "retrieval"
    assert "patterns" in result["trace"][0]
