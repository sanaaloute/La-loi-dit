"""Generation tools: drafting, translation and disclaimer helpers."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.tools.base import tool
from backend.agents.tools.registry import register_tool


class DraftAnswerArgs(BaseModel):
    question: str
    evidence_summary: str
    language: str = "fr"


class TranslateToLanguageArgs(BaseModel):
    text: str
    target_language: str


class SummarizeEvidenceArgs(BaseModel):
    evidence_text: str


class ApplyDisclaimerArgs(BaseModel):
    text: str
    language: str = "fr"


@tool("draft_answer", "Draft a grounded answer from a summary of evidence.")
async def draft_answer(ctx: Any, state: Any, args: DraftAnswerArgs) -> str:
    # This tool is a no-op at the code level: the actual drafting is done by the
    # response generator LLM.  It is exposed as a tool so the agent can explicitly
    # signal that it is ready to draft.
    return f"Brouillon demandé pour la question en {args.language} sur la base des preuves fournies."


@tool("translate_to_language", "Translate a French answer into another language.")
async def translate_to_language(ctx: Any, state: Any, args: TranslateToLanguageArgs) -> str:
    # Placeholder: the response generator LLM handles translation inline.
    return args.text


@tool("summarize_evidence", "Produce a short summary of the evidence for the reasoning agent.")
async def summarize_evidence(ctx: Any, state: Any, args: SummarizeEvidenceArgs) -> str:
    lines = args.evidence_text.strip().splitlines()
    summary = " | ".join(line[:120] for line in lines[:5])
    return summary


_DISCLAIMER_FR = (
    "\n\n---\nAvertissement : cette réponse est une aide à la recherche juridique "
    "fondée sur les sources citées. Elle ne constitue pas un conseil juridique. "
    "Consultez un professionnel du droit pour votre situation particulière."
)

_DISCLAIMER_EN = (
    "\n\n---\nDisclaimer: this answer is legal research assistance grounded in the "
    "cited sources. It is not legal advice. Consult a licensed legal professional "
    "for your specific situation."
)


@tool("apply_disclaimer", "Append the mandatory legal disclaimer to an answer.")
async def apply_disclaimer(ctx: Any, state: Any, args: ApplyDisclaimerArgs) -> str:
    disclaimer = _DISCLAIMER_EN if args.language.startswith("en") else _DISCLAIMER_FR
    text = args.text.rstrip()
    if disclaimer.strip() not in text:
        text = text + disclaimer
    return text


register_tool(draft_answer)
register_tool(translate_to_language)
register_tool(summarize_evidence)
register_tool(apply_disclaimer)
