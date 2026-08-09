"""Retrieval Agent.

Runs the retrieval plan in parallel through the coordinator, merges results
with any existing evidence, and updates the retry counter.  Uses the
``execute_retrieval_plan`` tool.

Parallel fan-out variant (LangGraph ``Send``): one ``retrieval_branch`` node
per decomposed sub-question runs concurrently; ``retrieval_merge`` fuses the
branches. Branch results travel on additive state channels
(``branch_evidence`` / ``branch_trace``) because plain channels cannot be
written by concurrent nodes.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.core.context import AppContext
from backend.core.models import EvidenceChunk, SearchKind, SearchTask
from backend.core.state import GraphState


class RetrievalAgent(Agent):
    """Executes all search tasks from the plan and accumulates evidence."""

    name = "retrieval_coordinator"
    system_prompt = (
        "You are the retrieval agent. Execute every planned search task in parallel, "
        "deduplicate and merge the results, and return the unified evidence list."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        tasks = list(state.get("tasks", []))
        existing = list(state.get("evidence", []))
        errors = list(state.get("errors", []))
        new_chunks: list[Any] = []
        if ctx.retriever is not None and tasks:
            task_dicts = [t.model_dump(mode="json") for t in tasks]
            call = ToolCall(name="execute_retrieval_plan", arguments={"tasks": task_dicts})
            results = await execute_tool_calls(TOOL_REGISTRY, [call], ctx, state)
            result = results[0]
            if result.error:
                errors.append(f"retrieval_error: {result.error}")
            else:
                new_chunks = list(result.output or [])

        merged = {c.chunk_id: c for c in [*existing, *new_chunks]}
        retries = state.get("retrieval_retries", 0)
        if state.get("needs_more_retrieval") or (
            state.get("reflection") and state["reflection"].should_retry_retrieval
        ):
            retries += 1

        return {
            "evidence": list(merged.values()),
            "retrieval_retries": retries,
            "needs_more_retrieval": False,
            "errors": errors,
            "trace": [
                *state.get("trace", []),
                f"retrieval_coordinator: {len(tasks)} tasks -> {len(new_chunks)} chunks ({len(merged)} total after merge)",
            ],
        }


retrieval_coordinator_node = RetrievalAgent().run


# ---------------------------------------------------------------------------
# Parallel fan-out retrieval: one branch per decomposed sub-question.
# ---------------------------------------------------------------------------


class RetrievalBranchAgent(Agent):
    """One parallel search branch: vector + keyword for a single sub-question."""

    name = "retrieval_branch"
    system_prompt = (
        "You are one parallel retrieval branch. Search the corpus for your "
        "assigned sub-question (vector + keyword) and return your evidence; "
        "the merge node fuses all branches."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        branch_query = state.get("branch_query") or state["query"]
        index = state.get("branch_index", 0)
        top_k = ctx.settings.default_top_k
        tasks = [
            SearchTask(kind=SearchKind.VECTOR, query=branch_query, top_k=top_k),
            SearchTask(kind=SearchKind.KEYWORD, query=branch_query, top_k=top_k),
        ]
        # Attach any planned auxiliary tasks (government, regulation...) that
        # target this branch's sub-question.
        planned = [
            t for t in state.get("tasks", [])
            if t.query == branch_query and t.kind not in (SearchKind.VECTOR, SearchKind.KEYWORD)
        ]
        tasks.extend(planned)

        chunks: list[EvidenceChunk] = []
        trace_line = ""
        if ctx.retriever is not None:
            try:
                chunks = await ctx.retriever.retrieve(tasks)
            except Exception as exc:
                trace_line = f" (erreur: {exc})"
        label = branch_query if len(branch_query) <= 60 else branch_query[:57] + "..."
        return {
            "branch_evidence": chunks,
            "branch_trace": [f"retrieval_branch {index + 1}: '{label}' -> {len(chunks)} chunks{trace_line}"],
        }


class RetrievalMergeAgent(Agent):
    """Fuses the parallel branch results into the unified evidence list."""

    name = "retrieval_coordinator"  # trace continuity with the single-node version
    system_prompt = (
        "You are the retrieval merge node. Fuse the evidence of all parallel "
        "search branches, deduplicate, and return the unified evidence list."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        existing = list(state.get("evidence", []))
        branch_chunks = list(state.get("branch_evidence", []))
        branch_traces = list(state.get("branch_trace", []))
        merged = {c.chunk_id: c for c in [*existing, *branch_chunks]}

        retries = state.get("retrieval_retries", 0)
        if state.get("needs_more_retrieval") or (
            state.get("reflection") and state["reflection"].should_retry_retrieval
        ):
            retries += 1

        # branch_trace accumulates across retry passes (additive channel), so
        # count the branches from the plan rather than from the trace lines.
        plan = state.get("plan")
        branch_count = len([q for q in (plan.sub_questions if plan else []) if q.strip()]) or 1

        return {
            "evidence": list(merged.values()),
            "retrieval_retries": retries,
            "needs_more_retrieval": False,
            "trace": [
                *state.get("trace", []),
                *branch_traces,
                f"retrieval_coordinator: {branch_count} parallel branch(es) -> {len(branch_chunks)} chunks ({len(merged)} total after merge)",
            ],
        }


retrieval_branch_node = RetrievalBranchAgent().run
retrieval_merge_node = RetrievalMergeAgent().run
