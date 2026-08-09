"""Reasoning Agent.

Reads the ranked evidence, identifies what is established, what is missing and
any contradictions.  May request one bounded retrieval retry when evidence is
insufficient.  This is a completion agent: the LLM reasons in prose and the
node translates the prose into state updates.
"""

from __future__ import annotations

from typing import Any

from backend.agents.agent import CompletionAgent
from backend.core.context import AppContext
from backend.core.state import GraphState
from backend.ingestion.text_cleaning import repair_extraction_artifacts


class ReasoningAgent(CompletionAgent):
    """Reasons over the ranked evidence and signals missing evidence."""

    name = "reasoning_agent"
    system_prompt = """You are the reasoning agent of an expert legal research assistant for Burkina Faso.

SCOPE
- You reason ONLY over the verified evidence excerpts provided with the question.
- You never use outside knowledge and never invent legal provisions, article
  numbers, dates or case law.

TASK
Analyze the evidence in relation to the user's question:
1. ESTABLISHED — what the evidence actually proves, referring to excerpts as [1], [2], ...
2. APPLICABLE RULES — which articles/provisions govern the question and how they combine.
3. GAPS — what the question needs that the evidence does not cover.
4. CONTRADICTIONS — any disagreement between sources (note which source is more
   authoritative or more recent).

OUTPUT
- If the evidence is sufficient: a concise, structured analysis (5-15 lines) in the
  user's language, citing the excerpts with [n].
- If the evidence is insufficient: start your answer with exactly "INSUFFICIENT:"
  and state precisely what is missing (which document, article or point), so that
  retrieval can be retried.

The excerpts come from PDF extraction and may contain artifacts (e.g. "consente - ment",
"lie u"); interpret them as the clean French words they stand for."""

    def _build_user_message(self, state: GraphState) -> str:
        evidence = list(state.get("ranked_evidence", []))
        if not evidence:
            return f"Question: {state['query']}\n\nNo evidence was retrieved."
        evidence_text = "\n\n".join(
            f"[{i}] {c.citation_label()} ({c.publication_date or 'date inconnue'}): "
            f"{repair_extraction_artifacts(c.content)[:2000]}"
            for i, c in enumerate(evidence[:10], start=1)
        )
        return f"Question: {state['query']}\n\nPreuves:\n{evidence_text}"

    def _parse_final(self, text: str, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        retries = state.get("retrieval_retries", 0)
        max_retries = ctx.settings.max_retrieval_retries
        needs_more = text.strip().lower().startswith("insufficient:")
        if needs_more and retries < max_retries and not state.get("needs_more_retrieval"):
            return {
                "needs_more_retrieval": True,
                "tasks": state.get("plan", None) and state["plan"].tasks or state.get("tasks", []),
                "reasoning_notes": text.strip(),
                "trace": [*state.get("trace", []), "reasoning: insufficient evidence, retry requested"],
            }
        return {
            "reasoning_notes": text.strip(),
            "needs_more_retrieval": False,
            "trace": [*state.get("trace", []), f"reasoning: analyzed {len(state.get('ranked_evidence', []))} evidence chunks"],
        }


reasoning_agent_node = ReasoningAgent().run
