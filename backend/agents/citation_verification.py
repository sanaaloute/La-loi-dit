"""Citation Verification Agent.

Every citation in the draft must trace to a real retrieved evidence chunk.
Fabricated or unverifiable citations are automatically rejected (removed from the
answer and recorded as warnings) — never silently kept.
"""

from __future__ import annotations

import re
from typing import Any

from backend.agents.agent import Agent
from backend.agents.tools import TOOL_REGISTRY, ToolCall, execute_tool_calls
from backend.core.context import AppContext
from backend.core.models import Citation, EvidenceChunk
from backend.core.state import GraphState


_CITATION_RE = r"\[(\d+)\]"


def extract_citations(text: str, evidence: list[EvidenceChunk]) -> tuple[list[Citation], list[Citation]]:
    """Return (verified, rejected) citations in the draft against the evidence list.

    Citation [n] is verified when ``1 <= n <= len(evidence)``; otherwise it is
    rejected as fabricated.  The returned :class:`Citation` objects carry the
    resolved chunk id, document name, article and url for downstream rendering.
    """
    verified: list[Citation] = []
    rejected: list[Citation] = []
    seen: set[int] = set()
    for match in re.finditer(_CITATION_RE, text or ""):
        idx = int(match.group(1))
        if idx in seen:
            continue
        seen.add(idx)
        label = match.group(0)
        if 1 <= idx <= len(evidence):
            chunk = evidence[idx - 1]
            verified.append(
                Citation(
                    label=label,
                    chunk_id=chunk.chunk_id,
                    document_name=chunk.document_name,
                    article=chunk.article,
                    url=chunk.url,
                    verified=True,
                )
            )
        else:
            rejected.append(Citation(label=label, chunk_id=None, document_name="", verified=False))
    return verified, rejected


class CitationVerificationAgent(Agent):
    """Verifies citations and removes any that are not grounded in evidence."""

    name = "citation_verification"
    system_prompt = (
        "You are the citation verification agent. Check every bracket citation "
        "in the draft against the retrieved evidence. Remove citations that do not "
        "point to a real evidence chunk and record them as warnings."
    )

    async def run(self, state: GraphState, ctx: AppContext) -> dict[str, Any]:
        draft = state.get("draft_answer", "")
        evidence = list(state.get("ranked_evidence", []))

        if not draft:
            return {
                "verified_citations": [],
                "citation_accuracy": 1.0,
                "draft_answer": draft,
                "trace": [*state.get("trace", []), "citation_verification: no draft to verify"],
            }

        verify_call = ToolCall(name="verify_citations", arguments={"text": draft})
        results = await execute_tool_calls(TOOL_REGISTRY, [verify_call], ctx, state)
        result = results[0]
        if result.error:
            return {
                "verified_citations": [],
                "citation_accuracy": 1.0,
                "draft_answer": draft,
                "errors": [*state.get("errors", []), f"citation_verification: {result.error}"],
                "trace": [*state.get("trace", []), "citation_verification: verification failed"],
            }

        output = result.output
        verified = output.get("verified", [])
        rejected = output.get("rejected", [])
        accuracy = output.get("accuracy", 1.0)

        cleaned = draft
        if rejected:
            remove_call = ToolCall(
                name="remove_invalid_citations",
                arguments={"draft": draft, "rejected_labels": [r["label"] for r in rejected]},
            )
            remove_results = await execute_tool_calls(TOOL_REGISTRY, [remove_call], ctx, state)
            cleaned = remove_results[0].output if not remove_results[0].error else draft

        warnings = [f"citation rejetée (non vérifiable): {r['label']}" for r in rejected]

        # Post-synthesis review: this node now runs AFTER response_generator,
        # so sync the verdict into the FinalAnswer — strip rejected citations
        # from the answer text and scale confidence by citation accuracy.
        final = state.get("final_answer")
        if final is not None:
            if cleaned != draft:
                final.answer = cleaned
            if accuracy < 1.0:
                # Scale the coverage-aware confidence down by citation accuracy.
                final.confidence = round(final.confidence * accuracy, 2)
                if final.confidence_breakdown is not None:
                    # Post-verification citation accuracy (spec §39).
                    final.confidence_breakdown.citation_confidence = round(accuracy, 2)
            if warnings:
                final.warnings.extend(warnings)

        return {
            "draft_answer": cleaned,
            "verified_citations": verified,
            "citation_accuracy": accuracy,
            **({"final_answer": final} if final is not None else {}),
            "trace": [
                *state.get("trace", []),
                f"citation_verification: {len(verified)} verified, {len(rejected)} rejected (accuracy {accuracy:.0%})",
            ],
            "errors": [*state.get("errors", []), *warnings],
        }


citation_verification_node = CitationVerificationAgent().run
