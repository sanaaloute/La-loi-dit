"""LangGraph multi-agent workflow.

Pipeline (see docs/architecture.md for the Mermaid diagram):

    user -> input_guardrail -> planner -> context_agent -> memory_agent
         -> retrieval_coordinator -> conflict_resolver -> evidence_ranking
         -> reasoning_agent -> reflection_agent -> citation_verification
         -> response_generator -> output_guardrail -> final answer

Bounded loops (max retry = 1 each):
    reasoning -> retrieval_coordinator  (missing evidence, one retry)
    reflection -> retrieval_coordinator (self-critique, one iteration)
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Any, AsyncIterator, Optional

from langgraph.graph import END, START, StateGraph

from backend.agents import (
    citation_verification,
    conflict_resolver,
    context_agent,
    evidence_ranking,
    input_guardrail,
    memory_agent,
    output_guardrail,
    reasoning_agent,
    reflection_agent,
    refusal,
    response_generator,
    retrieval_node,
)
from backend.core.context import AppContext
from backend.core.models import ChatMessage, ChatResponse, FinalAnswer
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

    def bind(fn):
        async def node(state: GraphState) -> dict[str, Any]:
            # Per-request LLM override (tier-gated, set by the API layer):
            # hand nodes a shallow context copy carrying the override.
            effective_ctx = ctx
            llm_override = state.get("llm")
            if llm_override is not None:
                effective_ctx = replace(ctx, llm=llm_override)
            return await fn(state, effective_ctx)

        return node

    def _route_after_guardrail(state: GraphState) -> str:
        guardrail = state.get("guardrail")
        return "refusal" if guardrail and not guardrail.allowed else "planner"

    def _route_after_reasoning(state: GraphState) -> str:
        if state.get("needs_more_retrieval") and state.get("retrieval_retries", 0) < max_retrieval_retries:
            return "retrieval_coordinator"
        return "reflection_agent"

    def _route_after_reflection(state: GraphState) -> str:
        reflection = state.get("reflection")
        if (
            reflection
            and reflection.should_retry_retrieval
            and state.get("reflection_count", 0) <= max_reflection_iterations
            and state.get("retrieval_retries", 0) < max_retrieval_retries
        ):
            return "retrieval_coordinator"
        return "citation_verification"

    g = StateGraph(GraphState)
    g.add_node("input_guardrail", bind(input_guardrail.input_guardrail_node))
    g.add_node("refusal", bind(refusal.refusal_node))
    g.add_node("planner", bind(planner_node))
    g.add_node("context_agent", bind(context_agent.context_agent_node))
    g.add_node("memory_agent", bind(memory_agent.memory_agent_node))
    g.add_node("retrieval_coordinator", bind(retrieval_node.retrieval_coordinator_node))
    g.add_node("conflict_resolver", bind(conflict_resolver.conflict_resolver_node))
    g.add_node("evidence_ranking", bind(evidence_ranking.evidence_ranking_node))
    g.add_node("reasoning_agent", bind(reasoning_agent.reasoning_agent_node))
    g.add_node("reflection_agent", bind(reflection_agent.reflection_agent_node))
    g.add_node("citation_verification", bind(citation_verification.citation_verification_node))
    g.add_node("response_generator", bind(response_generator.response_generator_node))
    g.add_node("output_guardrail", bind(output_guardrail.output_guardrail_node))

    g.add_edge(START, "input_guardrail")
    g.add_conditional_edges("input_guardrail", _route_after_guardrail)
    g.add_edge("refusal", END)
    g.add_edge("planner", "context_agent")
    g.add_edge("context_agent", "memory_agent")
    g.add_edge("memory_agent", "retrieval_coordinator")
    g.add_edge("retrieval_coordinator", "conflict_resolver")
    g.add_edge("conflict_resolver", "evidence_ranking")
    g.add_edge("evidence_ranking", "reasoning_agent")
    g.add_conditional_edges("reasoning_agent", _route_after_reasoning)
    g.add_conditional_edges("reflection_agent", _route_after_reflection)
    g.add_edge("citation_verification", "response_generator")
    g.add_edge("response_generator", "output_guardrail")
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
    final_state = await graph.ainvoke(state, config=config)
    latency_ms = (time.perf_counter() - started) * 1000
    answer: FinalAnswer = final_state["final_answer"]

    if ctx.memory is not None:
        try:
            await ctx.memory.append_turn(
                final_state["session_id"],
                final_state.get("user_id", "anonymous"),
                [
                    ChatMessage(role="user", content=final_state["query"]),
                    # Full FinalAnswer JSON: the history API parses it back;
                    # prompt consumers unwrap it via plain_message_content().
                    ChatMessage(role="assistant", content=answer.model_dump_json()),
                ],
            )
        except Exception:
            pass  # memory persistence must never break the answer path

    return ChatResponse(
        session_id=final_state["session_id"],
        answer=answer,
        trace=final_state.get("trace", []),
        latency_ms=round(latency_ms, 1),
    )


async def stream_query(graph, state: GraphState, *, config: Optional[dict[str, Any]] = None) -> AsyncIterator[dict[str, Any]]:
    """Yields per-node events for SSE/WebSocket streaming."""
    async for event in graph.astream(state, config=config, stream_mode="updates"):
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
