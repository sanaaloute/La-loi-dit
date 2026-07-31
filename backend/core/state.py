"""LangGraph shared state.

`total=False` so every node returns only the keys it updates. Retry counters
are explicit to enforce the "max retry = 1" policy inside the graph.
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from backend.core.models import (
    Citation,
    ConflictReport,
    EvidenceChunk,
    FinalAnswer,
    GuardrailResult,
    ReflectionResult,
    RetrievalPlan,
    SearchTask,
)


class GraphState(TypedDict, total=False):
    # --- input ---
    query: str
    user_id: str
    session_id: str
    language: str  # response language requested by the user
    scenario_date: Optional[str]  # ISO date for legal timeline reasoning
    llm: Any  # per-request LLMClient override (tier-gated); nodes fall back to ctx.llm

    # --- pipeline artefacts ---
    guardrail: GuardrailResult
    plan: RetrievalPlan
    conversation_context: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    user_preferences: dict[str, Any]
    tasks: list[SearchTask]
    evidence: list[EvidenceChunk]
    conflicts: list[ConflictReport]
    ranked_evidence: list[EvidenceChunk]
    reasoning_notes: str
    draft_answer: str
    reflection: ReflectionResult
    verified_citations: list[Citation]
    citation_accuracy: float
    final_answer: FinalAnswer

    # --- control / bookkeeping ---
    planning_retries: int
    retrieval_retries: int
    reflection_count: int
    needs_more_retrieval: bool
    errors: list[str]
    trace: list[str]  # human-readable node execution trail
