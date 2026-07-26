"""Citation Verification Agent.

Every citation in the draft must trace to a real retrieved evidence chunk.
Fabricated or unverifiable citations are automatically rejected (removed
from the answer and recorded as warnings) — never silently kept.
"""

from __future__ import annotations

import re
from typing import Any

from backend.core.context import AppContext
from backend.core.models import Citation
from backend.core.state import GraphState

_CITATION_RE = re.compile(r"\[(\d+)\]")


def extract_citations(text: str, evidence: list) -> tuple[list[Citation], list[Citation]]:
    """Split draft citations into (verified, rejected) against the evidence list."""
    verified: list[Citation] = []
    rejected: list[Citation] = []
    seen: set[int] = set()
    for match in _CITATION_RE.finditer(text or ""):
        idx = int(match.group(1))
        if idx in seen:
            continue
        seen.add(idx)
        if 1 <= idx <= len(evidence):
            chunk = evidence[idx - 1]
            verified.append(
                Citation(
                    label=match.group(0),
                    chunk_id=chunk.chunk_id,
                    document_name=chunk.document_name,
                    article=chunk.article,
                    url=chunk.url,
                    verified=True,
                )
            )
        else:
            rejected.append(Citation(label=match.group(0), verified=False))
    return verified, rejected


async def citation_verification_node(state: GraphState, ctx: AppContext) -> dict[str, Any]:
    draft = state.get("draft_answer", "")
    evidence = state.get("ranked_evidence", [])
    verified, rejected = extract_citations(draft, evidence)
    total = len(verified) + len(rejected)
    accuracy = len(verified) / total if total else 1.0

    cleaned = draft
    for bad in rejected:
        cleaned = cleaned.replace(bad.label, "")
    warnings = [f"citation rejetée (non vérifiable): {c.label}" for c in rejected]

    return {
        "draft_answer": cleaned,
        "verified_citations": verified,
        "citation_accuracy": accuracy,
        "trace": [
            *state.get("trace", []),
            f"citation_verification: {len(verified)} verified, {len(rejected)} rejected "
            f"(accuracy {accuracy:.0%})",
        ],
        "errors": [*state.get("errors", []), *warnings],
    }
