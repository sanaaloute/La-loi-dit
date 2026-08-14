"""Reasoning Agent.

Reads the ranked evidence, identifies what is established, what is missing and
any contradictions.  May request one bounded retrieval retry when evidence is
insufficient.  This is a completion agent: the LLM reasons in prose and the
node translates the prose into state updates.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.agents.agent import CompletionAgent
from backend.agents.context_agent import format_memory_sections
from backend.core.config import get_settings
from backend.core.context import AppContext
from backend.core.prompts import PromptRef
from backend.core.state import GraphState
from backend.ingestion.text_cleaning import repair_extraction_artifacts


class ReasoningAgent(CompletionAgent):
    """Reasons over the ranked evidence and signals missing evidence."""

    name = "reasoning_agent"
    # Resolved through the prompt registry (backend.core.prompts.REASONING_SYSTEM)
    # at every access, so Settings.prompts_dir overrides apply.
    system_prompt = PromptRef("REASONING_SYSTEM")

    def _build_user_message(self, state: GraphState, ctx: Optional[AppContext] = None) -> str:
        settings = ctx.settings if ctx is not None else get_settings()
        memory_sections = format_memory_sections(state, max_entry_chars=settings.context_message_max_chars)
        prefix = f"{memory_sections}\n\n" if memory_sections else ""
        evidence = list(state.get("ranked_evidence", []))
        if not evidence:
            return f"{prefix}Question: {state['query']}\n\nNo evidence was retrieved."
        # ranked_evidence is already bounded per sub-question by the ranking
        # node; every kept chunk reaches the prompt.
        evidence_text = "\n\n".join(
            f"[{i}] {c.citation_label()} ({c.publication_date or 'date inconnue'}): "
            f"{repair_extraction_artifacts(c.content)[: settings.reasoning_max_excerpt_chars]}"
            for i, c in enumerate(evidence, start=1)
        )
        return f"{prefix}Question: {state['query']}\n\nPreuves:\n{evidence_text}"

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
