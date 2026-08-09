"""Verification tools: citation handling and conflict detection."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from backend.agents.tools.base import tool
from backend.agents.tools.registry import register_tool


class ExtractCitationsArgs(BaseModel):
    text: str


class VerifyCitationsArgs(BaseModel):
    text: str


class DetectContradictionsArgs(BaseModel):
    chunk_ids: list[str]


class ResolveConflictByAuthorityArgs(BaseModel):
    kept_chunk_id: str
    dropped_chunk_id: str
    reason: str


_CITATION_RE = r"\[(\d+)\]"


@tool("extract_citations", "Extract bracket citations [1], [2], ... from a draft answer.")
async def extract_citations_tool(ctx: Any, state: Any, args: ExtractCitationsArgs) -> list[dict[str, Any]]:
    # Use the canonical Citation-producing function from the agent module.
    from backend.agents.citation_verification import extract_citations

    evidence = getattr(state, "ranked_evidence", None)
    if evidence is None:
        evidence = state.get("ranked_evidence", []) if isinstance(state, dict) else []
    verified, rejected = extract_citations(args.text or "", evidence)
    return [c.model_dump(mode="json") for c in verified + rejected]


@tool("verify_citations", "Verify that each citation in the draft maps to an available evidence chunk.")
async def verify_citations(ctx: Any, state: Any, args: VerifyCitationsArgs) -> dict[str, Any]:
    evidence = getattr(state, "ranked_evidence", None)
    if evidence is None:
        evidence = state.get("ranked_evidence", []) if isinstance(state, dict) else []
    if not evidence:
        return {"verified": [], "rejected": [], "accuracy": 1.0}

    import re

    verified, rejected = [], []
    seen: set[int] = set()
    for match in re.finditer(_CITATION_RE, args.text or ""):
        idx = int(match.group(1))
        if idx in seen:
            continue
        seen.add(idx)
        if 1 <= idx <= len(evidence):
            chunk = evidence[idx - 1]
            verified.append(
                {
                    "label": match.group(0),
                    "chunk_id": chunk.chunk_id,
                    "document_name": chunk.document_name,
                    "article": chunk.article,
                    "url": chunk.url,
                    "verified": True,
                }
            )
        else:
            rejected.append({"label": match.group(0), "verified": False})
    total = len(verified) + len(rejected)
    accuracy = len(verified) / total if total else 1.0
    return {"verified": verified, "rejected": rejected, "accuracy": accuracy}


@tool("detect_contradictions", "Detect whether two evidence chunks disagree on the same legal article.")
async def detect_contradictions(ctx: Any, state: Any, args: DetectContradictionsArgs) -> list[dict[str, Any]]:
    # The conflict_resolver node performs the heavy lifting; this tool exposes it.
    from backend.agents.conflict_resolver import _claims, _conflict_key, _contradict, resolve_pair

    evidence = getattr(state, "ranked_evidence", None)
    if evidence is None:
        evidence = state.get("ranked_evidence", []) if isinstance(state, dict) else []
    chunks = [c for c in evidence if c.chunk_id in args.chunk_ids]
    contradictions = []
    for i in range(len(chunks)):
        for j in range(i + 1, len(chunks)):
            a, b = chunks[i], chunks[j]
            key_a = _conflict_key(a)
            key_b = _conflict_key(b)
            if key_a and key_a == key_b and _contradict(a, b):
                kept, dropped, reason, resolved = resolve_pair(a, b, None)
                contradictions.append(
                    {
                        "topic": f"{a.document_name} art. {a.article}",
                        "kept_chunk_id": kept.chunk_id,
                        "dropped_chunk_id": dropped.chunk_id,
                        "reason": reason,
                        "resolved": resolved,
                    }
                )
    return contradictions


@tool("resolve_conflict_by_authority", "Record a conflict resolution decision between two chunks.")
async def resolve_conflict_by_authority(ctx: Any, state: Any, args: ResolveConflictByAuthorityArgs) -> dict[str, Any]:
    return {
        "topic": "authority resolution",
        "kept_chunk_id": args.kept_chunk_id,
        "dropped_chunk_id": args.dropped_chunk_id,
        "reason": args.reason,
        "resolved": True,
    }


class RemoveInvalidCitationsArgs(BaseModel):
    draft: str
    rejected_labels: list[str]


@tool("remove_invalid_citations", "Remove bracket citations that could not be verified from the draft answer.")
async def remove_invalid_citations(ctx: Any, state: Any, args: RemoveInvalidCitationsArgs) -> str:
    cleaned = args.draft
    for bad_label in args.rejected_labels:
        cleaned = cleaned.replace(bad_label, "")
    return cleaned


register_tool(extract_citations_tool)
register_tool(verify_citations)
register_tool(detect_contradictions)
register_tool(resolve_conflict_by_authority)
register_tool(remove_invalid_citations)
