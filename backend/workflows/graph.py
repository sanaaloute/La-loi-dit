"""LangGraph multi-agent workflow.

Pipeline (see docs/architecture.md for the Mermaid diagram):

    user -> input_guardrail -> query_router
         -> (direct) response_generator -> output_guardrail
         -> (retrieval) planner -> context_agent -> memory_agent
         -> [fan-out: one retrieval_branch per sub-question, in parallel]
         -> retrieval_merge -> conflict_resolver -> evidence_ranking
         -> coverage_auditor -> reasoning_agent -> reflection_agent
         -> response_generator -> claim_verification -> citation_verification
         -> output_guardrail
         -> final answer

Bounded loops (max retry = 1 each):
    coverage_auditor -> retrieval fan-out (missing sub-questions, one retry)
    reasoning -> retrieval fan-out  (missing evidence, one retry)
    reflection -> retrieval fan-out (self-critique, one iteration)

Post-synthesis review: claim_verification and citation_verification run AFTER
response_generator so the judges actually see the drafted answer (citation
verification used to run before it and always verified an empty draft).
claim_verification runs FIRST so claims are built on the draft with its [n]
markers (they designate the intended sources); if citation_verification then
strips an unverifiable marker, the claim simply keeps its recorded support.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from backend.agents import (
    citation_verification,
    claim_verification,
    conflict_resolver,
    context_agent,
    coverage_auditor,
    evidence_ranking,
    input_guardrail,
    memory_agent,
    output_guardrail,
    parent_expansion,
    query_router,
    reasoning_agent,
    reflection_agent,
    refusal,
    response_generator,
    retrieval_node,
)
from backend.core.context import AppContext
from backend.core.model_roles import resolve_role_llm
from backend.core.models import ChatMessage, ChatResponse, FinalAnswer, QuestionType
from backend.core.state import GraphState
from backend.planner.agent import planner_node


# ---------------------------------------------------------------------------
# Routing functions (conditional edges)
# ---------------------------------------------------------------------------


def build_graph(ctx: AppContext):
    """Compile the StateGraph with the AppContext bound into every node."""
    settings = ctx.settings
    max_retrieval_retries = settings.max_retrieval_retries
    max_reflection_iterations = settings.max_reflection_iterations

    def bind(fn, role: Optional[str] = None):
        async def node(state: GraphState) -> dict[str, Any]:
            # Per-request LLM override (tier-gated, set by the API layer):
            # hand nodes a shallow context copy carrying the override.
            # `role` (spec §46) further binds the node to its role's model
            # when role routing is enabled; otherwise this is a pass-through.
            base_llm = state.get("llm")
            if base_llm is None:
                base_llm = ctx.llm
            effective_llm = resolve_role_llm(role, base_llm, settings) if role else base_llm
            effective_ctx = ctx if effective_llm is ctx.llm else replace(ctx, llm=effective_llm)
            return await fn(state, effective_ctx)

        return node

    def _route_after_guardrail(state: GraphState) -> str:
        guardrail = state.get("guardrail")
        return "refusal" if guardrail and not guardrail.allowed else "query_router"

    def _route_after_router(state: GraphState) -> str:
        """Direct answers short-circuit the whole retrieval pipeline."""
        return "response_generator" if state.get("route") == "direct" else "planner"

    def _fanout_retrieval(state: GraphState) -> list[Send]:
        """Fan out one parallel retrieval branch per decomposed sub-question.

        Each branch gets its own vector+keyword search for its sub-question;
        the merge node fuses all branch results before conflict resolution.
        Falls back to a single branch on the raw query when the plan has no
        decomposition.
        """
        plan = state.get("plan")
        sub_questions = [q for q in (plan.sub_questions if plan else []) if q.strip()]
        if not sub_questions:
            sub_questions = [state["query"]]
        return [
            Send("retrieval_branch", {**state, "branch_query": q, "branch_index": i})
            for i, q in enumerate(sub_questions)
        ]

    def _route_after_coverage(state: GraphState):
        """Coverage gap -> one bounded re-retrieval on the missing sub-questions.

        Uses the same mechanism as the reasoning/reflection retry paths: the
        node sets ``needs_more_retrieval`` (only when the retry budget allows
        it) and ``retrieval_merge`` counts the pass. The fan-out targets the
        auditor's missing issues rather than the whole plan.

        Fast lane: simple FACTUAL/DEFINITION questions with decent coverage
        and no unresolved conflict skip the two serial analysis LLM calls
        (reasoning, reflection) and go straight to synthesis.
        """
        report = state.get("coverage_report")
        if (
            report is not None
            and state.get("needs_more_retrieval")
            and state.get("retrieval_retries", 0) < max_retrieval_retries
        ):
            issues = report.missing_issues or [state["query"]]
            return [
                Send("retrieval_branch", {**state, "branch_query": q, "branch_index": i})
                for i, q in enumerate(issues)
            ]
        if _fast_lane_eligible(state):
            return "response_generator"
        return "reasoning_agent"

    _FAST_LANE_TYPES = frozenset({QuestionType.FACTUAL, QuestionType.DEFINITION})

    def _fast_lane_eligible(state: GraphState) -> bool:
        if not settings.fast_lane_enabled:
            return False
        plan = state.get("plan")
        if plan is None or plan.question_type not in _FAST_LANE_TYPES:
            return False
        if any(not c.resolved for c in state.get("conflicts", [])):
            return False
        report = state.get("coverage_report")
        return report is not None and report.coverage >= settings.fast_lane_min_coverage

    def _route_after_reasoning(state: GraphState):
        if state.get("needs_more_retrieval") and state.get("retrieval_retries", 0) < max_retrieval_retries:
            return _fanout_retrieval(state)
        return "reflection_agent"

    def _route_after_reflection(state: GraphState):
        reflection = state.get("reflection")
        if (
            reflection
            and reflection.should_retry_retrieval
            and state.get("reflection_count", 0) <= max_reflection_iterations
            and state.get("retrieval_retries", 0) < max_retrieval_retries
        ):
            return _fanout_retrieval(state)
        return "response_generator"

    def _route_after_response(state: GraphState) -> str:
        """Direct answers skip claim/citation verification (no evidence to
        verify against) but still pass through the output guardrail."""
        return "output_guardrail" if state.get("route") == "direct" else "claim_verification"

    g = StateGraph(GraphState)
    g.add_node("input_guardrail", bind(input_guardrail.input_guardrail_node))
    g.add_node("refusal", bind(refusal.refusal_node))
    g.add_node("query_router", bind(query_router.query_router_node, role="classification"))
    g.add_node("planner", bind(planner_node, role="planner"))
    g.add_node("context_agent", bind(context_agent.context_agent_node, role="classification"))
    g.add_node("memory_agent", bind(memory_agent.memory_agent_node, role="classification"))
    g.add_node("retrieval_branch", bind(retrieval_node.retrieval_branch_node))
    g.add_node("retrieval_merge", bind(retrieval_node.retrieval_merge_node))
    g.add_node("conflict_resolver", bind(conflict_resolver.conflict_resolver_node))
    g.add_node("evidence_ranking", bind(evidence_ranking.evidence_ranking_node))
    g.add_node("parent_expansion", bind(parent_expansion.parent_expansion_node))
    g.add_node("coverage_auditor", bind(coverage_auditor.coverage_auditor_node))
    g.add_node("reasoning_agent", bind(reasoning_agent.reasoning_agent_node, role="analysis"))
    g.add_node("reflection_agent", bind(reflection_agent.reflection_agent_node, role="analysis"))
    g.add_node("citation_verification", bind(citation_verification.citation_verification_node))
    g.add_node("claim_verification", bind(claim_verification.claim_verification_node))
    g.add_node("response_generator", bind(response_generator.response_generator_node, role="synthesis"))
    g.add_node("output_guardrail", bind(output_guardrail.output_guardrail_node))

    g.add_edge(START, "input_guardrail")
    g.add_conditional_edges("input_guardrail", _route_after_guardrail)
    g.add_edge("refusal", END)
    g.add_conditional_edges("query_router", _route_after_router)
    g.add_edge("planner", "context_agent")
    g.add_edge("context_agent", "memory_agent")
    g.add_conditional_edges("memory_agent", _fanout_retrieval)
    g.add_edge("retrieval_branch", "retrieval_merge")
    g.add_edge("retrieval_merge", "conflict_resolver")
    g.add_edge("conflict_resolver", "parent_expansion")
    g.add_edge("parent_expansion", "evidence_ranking")
    g.add_edge("evidence_ranking", "coverage_auditor")
    g.add_conditional_edges("coverage_auditor", _route_after_coverage)
    g.add_conditional_edges("reasoning_agent", _route_after_reasoning)
    g.add_conditional_edges("reflection_agent", _route_after_reflection)
    g.add_conditional_edges("response_generator", _route_after_response)
    g.add_edge("claim_verification", "citation_verification")
    g.add_edge("citation_verification", "output_guardrail")
    g.add_edge("output_guardrail", END)
    return g.compile()


# ---------------------------------------------------------------------------
# Runner helpers (REST / SSE / WebSocket all funnel through these)
# ---------------------------------------------------------------------------


def initial_state(
    query: str,
    *,
    session_id: Optional[str] = None,
    user_id: str = "anonymous",
    language: Optional[str] = None,
    scenario_date: Optional[str] = None,
) -> GraphState:
    return GraphState(
        query=query,
        session_id=session_id or uuid.uuid4().hex,
        user_id=user_id,
        language=language or "",
        scenario_date=scenario_date,
        planning_retries=0,
        retrieval_retries=0,
        reflection_count=0,
        needs_more_retrieval=False,
        errors=[],
        trace=[],
    )


async def run_query(graph, ctx: AppContext, state: GraphState, *, config: Optional[dict[str, Any]] = None) -> ChatResponse:
    started = time.perf_counter()
    # Wall-clock time the question was received: the persisted prompt carries
    # this timestamp, the answer gets its own completion time — otherwise both
    # show the completion time (the turn is written after the run ends).
    received_at = datetime.now(timezone.utc)
    final_state = await graph.ainvoke(state, config=_with_recursion_limit(config))
    latency_ms = (time.perf_counter() - started) * 1000
    answer: FinalAnswer = final_state["final_answer"]

    if ctx.memory is not None:
        try:
            await ctx.memory.append_turn(
                final_state["session_id"],
                final_state.get("user_id", "anonymous"),
                [
                    ChatMessage(role="user", content=final_state["query"], created_at=received_at),
                    # Full FinalAnswer JSON: the history API parses it back;
                    # prompt consumers unwrap it via plain_message_content().
                    ChatMessage(role="assistant", content=answer.model_dump_json()),
                ],
            )
            # Long-term memory: compress the oldest turns into the session's
            # summary record once the buffer outgrows the short-term window
            # (fires only every memory_summary_max_turns new messages).
            from backend.memory.summarizer import maybe_summarize

            await maybe_summarize(
                ctx.memory,
                final_state["session_id"],
                llm=ctx.llm,
                user_id=final_state.get("user_id", "anonymous"),
            )
        except Exception:
            pass  # memory persistence must never break the answer path

    return ChatResponse(
        session_id=final_state["session_id"],
        answer=answer,
        trace=final_state.get("trace", []),
        latency_ms=round(latency_ms, 1),
    )


# LangGraph's default recursion_limit (25) counts supersteps, and this graph's
# legitimate worst case already sits at ~23-25: a full pass is ~16 nodes
# (including query_router and parent_expansion), plus one bounded retrieval
# retry pass (+5), plus the answer tail. 40 leaves headroom without enabling
# runaway loops — the retrieval retry budget remains the real guardrail.
GRAPH_RECURSION_LIMIT = 40


def _with_recursion_limit(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(config or {})
    merged.setdefault("recursion_limit", GRAPH_RECURSION_LIMIT)
    return merged


async def stream_query(graph, state: GraphState, *, config: Optional[dict[str, Any]] = None) -> AsyncIterator[dict[str, Any]]:
    """Yields per-node events for SSE/WebSocket streaming."""
    async for event in graph.astream(state, config=_with_recursion_limit(config), stream_mode="updates"):
        for node_name, update in event.items():
            yield {"node": node_name, "update": _serialize(update)}


def _serialize(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj
