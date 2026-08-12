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
    from backend.guardrails.input_guard import check_input as deterministic_check_input
    from backend.guardrails.llm_guard import scan_input as llm_guard_scan_input

    # First layer: deterministic regex guards (fast, offline, never downloads).
    det = await deterministic_check_input(args.query, ctx.settings)
    if not det.allowed:
        return {
            "allowed": False,
            "flags": [f.value for f in det.flags],
            "reasons": det.reasons,
            "sanitized_query": det.sanitized_query,
        }

    # Second layer: semantic LLM Guard scanners.
    text = det.sanitized_query if det.sanitized_query is not None else args.query
    result = await llm_guard_scan_input(text, ctx.settings, run_deterministic=False)

    # Merge deterministic and semantic findings.
    flags: list[str] = [f.value for f in det.flags]
    for f in result.flags:
        if f.value not in flags:
            flags.append(f.value)
    reasons = list(det.reasons)
    for r in result.reasons:
        if r not in reasons:
            reasons.append(r)
    sanitized = result.sanitized_query if result.sanitized_query is not None else text

    return {
        "allowed": result.allowed,
        "flags": flags,
        "reasons": reasons,
        "sanitized_query": sanitized,
    }


@tool("check_output", "Check the final answer for unsafe legal advice, policy violations and citation issues.")
async def check_output(ctx: Any, state: Any, args: CheckOutputArgs) -> dict[str, Any]:
    from backend.core.models import FinalAnswer, RiskFlag
    from backend.guardrails.output_guard import check_output as deterministic_check_output
    from backend.guardrails.llm_guard import scan_output as llm_guard_scan_output

    answer = FinalAnswer(answer=args.answer_text)
    evidence = getattr(state, "ranked_evidence", None)
    if evidence is None and isinstance(state, dict):
        evidence = state.get("ranked_evidence", [])
    result = await deterministic_check_output(answer, evidence or [], ctx.settings)

    # Second layer: semantic LLM Guard output scanners (advisory).
    sanitized, reasons, flags = await llm_guard_scan_output(
        result.answer, ctx.settings, run_deterministic=False
    )
    result.answer = sanitized
    for reason in reasons:
        if reason not in result.warnings:
            result.warnings.append(reason)
    if flags:
        result.metadata.setdefault("risk_flags", [])
        for flag in flags:
            if flag not in result.metadata["risk_flags"]:
                result.metadata["risk_flags"].append(flag)
        review_flags = {
            RiskFlag.UNSAFE_LEGAL_ADVICE.value,
            RiskFlag.HALLUCINATION_SUSPECT.value,
            RiskFlag.UNVERIFIED_SOURCE.value,
        }
        if review_flags.intersection(flags):
            result.requires_human_review = True

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
