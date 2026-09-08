"""Tests for the language gate (backend/agents/language_gate.py).

The corpus is French-only: the gate detects the query language and translates
non-French queries into French before routing/retrieval, keeping the user's
wording in ``original_query`` and the detected code in ``language``.  French
queries are recognized heuristically and never cost an LLM call; any failure
leaves the original query untouched (fail-safe, like the query router).
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.agents.language_gate import (
    LanguageGateAgent,
    _looks_french,
    _parse_translation,
)
from backend.workflows.graph import initial_state, run_query


class StubLLM:
    """Scripted LLM: returns a fixed output (or raises), records its calls."""

    def __init__(self, output: str = "", exc: Exception | None = None):
        self.output = output
        self.exc = exc
        self.calls = 0

    async def complete(self, system: str, user: str, temperature=None) -> str:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.output


def _ctx(settings, llm: StubLLM) -> SimpleNamespace:
    return SimpleNamespace(llm=llm, settings=settings)


# ---------------------------------------------------------------------------
# Heuristic French detection / translation parsing
# ---------------------------------------------------------------------------


def test_looks_french():
    assert _looks_french("Quels sont les délais de prescription ?")
    assert _looks_french("Préavis en cas de licenciement")  # diacritics only
    # Unaccented French: function words, not diacritics, carry the signal.
    assert _looks_french("Que peux tu faire pour moi ?")
    assert _looks_french("Comment est calculee la pension alimentaire")
    assert not _looks_french("What is the notice period for dismissal?")
    assert not _looks_french("What are my rights as a tenant?")


def test_parse_translation():
    assert _parse_translation('{"language": "en", "french": "Quel est le préavis ?"}') == {
        "language": "en",
        "french": "Quel est le préavis ?",
    }
    # Prose around the JSON is tolerated.
    assert _parse_translation('Voici: {"language": "en", "french": "Bonjour"}')["french"] == "Bonjour"
    assert _parse_translation("") is None
    assert _parse_translation("pas de json ici") is None
    assert _parse_translation('{"language": "en"}') is None
    assert _parse_translation('{"language": "en", "french": "  "}') is None


# ---------------------------------------------------------------------------
# Gate agent unit tests (stubbed LLM)
# ---------------------------------------------------------------------------


async def test_french_query_passes_through_without_llm_call(settings):
    llm = StubLLM(exc=AssertionError("LLM must not be called for French"))
    agent = LanguageGateAgent()
    result = await agent.run(
        {"query": "Quels sont les délais de prescription ?", "trace": []},
        _ctx(settings, llm),
    )
    assert llm.calls == 0
    assert "query" not in result  # untouched
    assert result["language"] == "fr"


async def test_non_french_query_is_translated(settings):
    llm = StubLLM('{"language": "en", "french": "Quel est le préavis en cas de licenciement ?"}')
    agent = LanguageGateAgent()
    result = await agent.run(
        {"query": "What is the notice period for dismissal?", "trace": []},
        _ctx(settings, llm),
    )
    assert llm.calls == 1
    assert result["query"] == "Quel est le préavis en cas de licenciement ?"
    assert result["original_query"] == "What is the notice period for dismissal?"
    assert result["language"] == "en"


async def test_explicit_client_language_wins_over_detection(settings):
    llm = StubLLM('{"language": "en", "french": "Quels sont mes droits ?"}')
    agent = LanguageGateAgent()
    result = await agent.run(
        {"query": "What are my rights?", "language": "wo", "trace": []},
        _ctx(settings, llm),
    )
    assert result["language"] == "wo"


async def test_llm_error_keeps_the_original_query(settings):
    llm = StubLLM(exc=TimeoutError("provider timeout"))
    agent = LanguageGateAgent()
    result = await agent.run(
        {"query": "What is the notice period for dismissal?", "trace": []},
        _ctx(settings, llm),
    )
    assert "query" not in result
    assert "original_query" not in result
    assert result["language"] == "fr"


async def test_garbage_translation_keeps_the_original_query(settings):
    llm = StubLLM("je ne peux pas traduire cela")
    agent = LanguageGateAgent()
    result = await agent.run(
        {"query": "What is the notice period for dismissal?", "trace": []},
        _ctx(settings, llm),
    )
    assert "query" not in result
    assert "original_query" not in result


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


async def test_language_gate_runs_before_the_router(graph, ctx):
    response = await run_query(graph, ctx, initial_state("bonjour"))
    trace = response.trace
    gate = next(i for i, t in enumerate(trace) if t.startswith("language_gate"))
    router = next(i for i, t in enumerate(trace) if t.startswith("query_router"))
    assert gate < router
    # Mock LLM translates nothing; the greeting still takes the direct route.
    assert any(t.startswith("query_router: direct") for t in trace)


async def test_english_legal_question_still_reaches_retrieval(graph, ctx):
    # Offline the mock translator echoes the query (no-op), the mock router
    # fails safe to retrieval, and the pipeline runs end to end.
    response = await run_query(
        graph, ctx, initial_state("What is the statute of limitations in Burkina Faso?")
    )
    assert any(t.startswith("language_gate") for t in response.trace)
    assert any(t.startswith("planner") for t in response.trace)
    assert any(t.startswith("retrieval") for t in response.trace)
