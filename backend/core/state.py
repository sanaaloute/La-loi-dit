"""LangGraph shared state.

`total=False` so every node returns only the keys it updates. Retry counters
are explicit to enforce the "max retry = 1" policy inside the graph.

Parallel retrieval fan-out: each `retrieval_branch` Send writes its own
`branch_evidence` / `branch_trace`, merged across branches with an additive
reducer; `retrieval_merge` then folds the branches into `evidence`.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

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


def merge_branch_chunks(
    old: list[EvidenceChunk] | None, new: list[EvidenceChunk]
) -> list[EvidenceChunk]:
    """Reducer for the parallel retrieval branches: dedup by chunk_id.

    Deduping (rather than plain concatenation) also keeps retry fan-outs from
    double-counting chunks already found in the first pass.
    """
    merged = {c.chunk_id: c for c in (old or [])}
    for chunk in new:
        merged[chunk.chunk_id] = chunk
    return list(merged.values())


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

    # --- parallel retrieval fan-out (additive reducers) ---
    branch_query: str  # set per-Send: the sub-question this branch searches
    branch_index: int  # set per-Send: 0-based branch number
    branch_evidence: Annotated[list[EvidenceChunk], merge_branch_chunks]
    branch_trace: Annotated[list[str], operator.add]

    # --- control / bookkeeping ---
    planning_retries: int
    retrieval_retries: int
    reflection_count: int
    needs_more_retrieval: bool
    errors: list[str]
    trace: list[str]  # human-readable node execution trail
