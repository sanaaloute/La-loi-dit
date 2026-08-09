"""Reflection Agent.

Self-critique before answering: did the analysis answer every part of the
question, was evidence missed, is every claim citable, could anything be
hallucinated, are there contradictions?  Produces a ReflectionResult and may
request one retrieval re-run (bounded by the global retry budget).
"""

from __future__ import annotations

import json
from typing import Any, Optional

from backend.agents.agent import CompletionAgent
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.models import ReflectionResult
from backend.core.prompts import PromptRef
from backend.core.state import GraphState


class ReflectionAgent(CompletionAgent):
    """Self-critique step before final answer generation."""

    name = "reflection_agent"
    # Resolved through the prompt registry (backend.core.prompts.REFLECTION_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("REFLECTION_SYSTEM")

    def _build_user_message(self, state: GraphState, ctx: Optional[AppContext] = None) -> str:
        settings = ctx.settings if ctx is not None else get_settings()
        evidence_text = "\n".join(
            c.citation_label()
            for c in state.get("ranked_evidence", [])[: settings.answer_max_evidence]
        )
        return (
            f"Question: {state['query']}\n"
            f"Analyse: {state.get('reasoning_notes', '')}\n"
            f"Preuves:\n{evidence_text}"
        )

    def _parse_final(self, text: str, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        result = _heuristic_reflection(state, ctx.settings)
        try:
            # Try to parse the LLM's JSON output; fall back to heuristic on failure.
            parsed = json.loads(text.strip()) if text.strip() else {}
            if not isinstance(parsed, dict):
                parsed = {}
            result = ReflectionResult(
                complete=parsed.get("complete", result.complete),
                answered_all_questions=parsed.get("answered_all_questions", result.answered_all_questions),
                all_claims_cited=parsed.get("all_claims_cited", result.all_claims_cited),
                contradictions_found=parsed.get("contradictions_found", result.contradictions_found),
                issues=parsed.get("issues", result.issues),
                should_retry_retrieval=parsed.get("should_retry_retrieval", result.should_retry_retrieval),
                retry_query=parsed.get("retry_query", result.retry_query),
            )
        except Exception:
            pass

        count = state.get("reflection_count", 0) + 1
        if count > ctx.settings.max_reflection_iterations:
            result.should_retry_retrieval = False
        return {
            "reflection": result,
            "reflection_count": count,
            "trace": [
                *state.get("trace", []),
                f"reflection ({count}/{ctx.settings.max_reflection_iterations}): "
                f"{'retry retrieval' if result.should_retry_retrieval else 'proceed to answer'}",
            ],
        }


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
        issues=([] if evidence else ["aucune preuve vérifiable disponible"])
        + [f"conflit non résolu: {c.topic}" for c in unresolved],
        should_retry_retrieval=should_retry,
        retry_query=state.get("query") if should_retry else None,
    )


reflection_agent_node = ReflectionAgent().run
