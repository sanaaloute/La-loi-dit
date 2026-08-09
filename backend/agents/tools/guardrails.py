"""Guardrail tools: input and output policy checks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from backend.agents.tools.base import tool
from backend.agents.tools.registry import register_tool


class CheckInputArgs(BaseModel):
    query: str


class CheckOutputArgs(BaseModel):
    answer_text: str
    evidence_text: str = ""


class ApplyConfidencePolicyArgs(BaseModel):
    confidence: float
    has_evidence: bool


@tool("check_input", "Check the user query for prompt injection, jailbreak, sensitive info and role hijacking.")
async def check_input(ctx: Any, state: Any, args: CheckInputArgs) -> dict[str, Any]:
    from backend.guardrails.input_guard import check_input

    result = await check_input(args.query, ctx.settings)
    return {
        "allowed": result.allowed,
        "flags": [f.value for f in result.flags],
        "reasons": result.reasons,
        "sanitized_query": result.sanitized_query,
    }


@tool("check_output", "Check the final answer for unsafe legal advice, policy violations and citation issues.")
async def check_output(ctx: Any, state: Any, args: CheckOutputArgs) -> dict[str, Any]:
    from backend.core.models import FinalAnswer
    from backend.guardrails.output_guard import check_output

    answer = FinalAnswer(answer=args.answer_text)
    evidence = getattr(state, "ranked_evidence", None)
    if evidence is None and isinstance(state, dict):
        evidence = state.get("ranked_evidence", [])
    result = await check_output(answer, evidence or [], ctx.settings)
    return {
        "refused": result.refused,
        "warnings": result.warnings,
        "requires_human_review": result.requires_human_review,
    }


@tool("apply_confidence_policy", "Apply the confidence thresholds (warning / human review) based on confidence and evidence.")
async def apply_confidence_policy(ctx: Any, state: Any, args: ApplyConfidencePolicyArgs) -> dict[str, Any]:
    warnings: list[str] = []
    requires_human_review = False
    confidence_threshold = ctx.settings.confidence_threshold
    human_review_threshold = ctx.settings.human_review_threshold
    if args.has_evidence:
        if args.confidence < human_review_threshold:
            requires_human_review = True
            warnings.append(
                f"confiance très faible ({args.confidence:.0%}): escalade vers un expert juridique"
            )
        elif args.confidence < confidence_threshold:
            warnings.append(f"confiance faible ({args.confidence:.0%})")
    return {
        "requires_human_review": requires_human_review,
        "warnings": warnings,
    }


register_tool(check_input)
register_tool(check_output)
register_tool(apply_confidence_policy)
