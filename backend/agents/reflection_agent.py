"""Reflection Agent: self-critique before answering.

Asks: did I answer every question? did I miss evidence? is everything
cited? could this hallucinate? is there contradiction? should retrieval
run again? Maximum one reflection iteration (MAX_REFLECTION_ITERATIONS = 1).
"""

from __future__ import annotations

from typing import Any

from backend.core.context import AppContext
from backend.core.models import ReflectionResult
from backend.core.state import GraphState

_SYSTEM = """You are the reflection agent of a legal research assistant. Self-critique:
did the analysis answer every part of the question? Was evidence missed?
Is every claim citable? Could anything be hallucinated? Are there contradictions?
Should retrieval run once more? Answer with the JSON schema only."""


def _heuristic_reflection(state: GraphState, settings) -> ReflectionResult:
    evidence = state.get("ranked_evidence", [])
    conflicts = state.get("conflicts", [])
    unresolved = [c for c in conflicts if not c.resolved]
    retries = state.get("retrieval_retries", 0)
    max_retries = settings.max_retrieval_retries
    should_retry = not evidence and retries < max_retries
    return ReflectionResult(
        complete=bool(evidence),
        answered_all_questions=bool(evidence),
        all_claims_cited=bool(evidence),
        contradictions_found=bool(unresolved),
        issues=(
            [] if evidence else ["aucune preuve vérifiable disponible"]
        ) + [f"conflit non résolu: {c.topic}" for c in unresolved],
        should_retry_retrieval=should_retry,
        retry_query=state.get("query") if should_retry else None,
    )


async def reflection_agent_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    settings = ctx.settings
    max_iterations = settings.max_reflection_iterations
    count = state.get("reflection_count", 0) + 1
    if ctx.llm.provider == "mock":
        result = _heuristic_reflection(state, settings)
    else:
        try:
            evidence_text = "\n".join(c.citation_label() for c in state.get("ranked_evidence", [])[:10])
            result = await ctx.llm.complete_json(
                _SYSTEM,
                f"Question: {state['query']}\nAnalyse: {state.get('reasoning_notes', '')}\n"
                f"Preuves:\n{evidence_text}",
                ReflectionResult,
            )
        except Exception:
            result = _heuristic_reflection(state, settings)
    if count > max_iterations:
        result.should_retry_retrieval = False
    return {
        "reflection": result,
        "reflection_count": count,
        "trace": [
            *state.get("trace", []),
            f"reflection ({count}/{max_iterations}): "
            f"{'retry retrieval' if result.should_retry_retrieval else 'proceed to answer'}",
        ],
    }
