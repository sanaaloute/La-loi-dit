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
    Claim,
    ConflictReport,
    CoverageReport,
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
    original_query: str  # user's untranslated wording, set by the language gate
    user_id: str
    session_id: str
    language: str  # response language requested by the user
    scenario_date: Optional[str]  # ISO date for legal timeline reasoning
    llm: Any  # per-request LLMClient override; nodes fall back to ctx.llm

    # --- pipeline artefacts ---
    guardrail: GuardrailResult
    route: str  # "retrieval" (default), "direct", or "general" — query router
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
    coverage_report: CoverageReport
    verified_citations: list[Citation]
    citation_accuracy: float
    claims: list[Claim]
    final_answer: FinalAnswer

    # --- parallel retrieval fan-out (additive reducers) ---
    branch_query: str  # set per-Send: the sub-question this branch searches
    branch_index: int  # set per-Send: 0-based branch number
    branch_evidence: Annotated[list[EvidenceChunk], merge_branch_chunks]
    branch_trace: Annotated[list[str], operator.add]
    # Which sub-question produced each chunk ({"chunk_id", "query"} entries);
    # evidence_ranking uses it to apply the per-sub-question evidence cap.
    branch_membership: Annotated[list[dict[str, str]], operator.add]

    # --- control / bookkeeping ---
    planning_retries: int
    retrieval_retries: int
    retrieval_retry_new: int  # chunks actually new in the last merge pass (0 = empty retry)
    reflection_count: int
    needs_more_retrieval: bool
    errors: list[str]
    trace: list[str]  # human-readable node execution trail

    # --- live token streaming (optional, SSE/WS only) ---
    # Per-run asyncio.Queue set by the streaming API layer; nodes push
    # ("reasoning_delta" | "delta" | "delta_reset", {...}) tuples through
    # emit_stream_event. Absent on REST runs and in tests (events dropped).
    stream_queue: Any
    # Accumulated reasoning text per agent (one "[node]\n…" entry each),
    # attached to the answer metadata so the thinking survives stream drops
    # and history reloads. Shared by reference across nodes.
    thinking_log: list[str]


def emit_stream_event(state: GraphState, kind: str, **fields: Any) -> None:
    """Push a live-stream event to the API layer; no-op outside streaming runs."""
    queue = state.get("stream_queue")
    if queue is not None:
        try:
            queue.put_nowait((kind, fields))
        except Exception:
            pass  # streaming must never break the answer path
