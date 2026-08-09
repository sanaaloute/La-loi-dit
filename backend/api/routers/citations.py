"""Citations router: resolve a citation/chunk id to its evidence record.

Goes straight to the vector store by primary key (``get_by_ids`` — a cheap
PK lookup in Milvus, a dict hit in the in-memory store): no similarity
search, no LLM. Spec §48.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.deps import get_ctx
from backend.core.models import CitationRecord, Role
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

router = APIRouter(prefix="/citations", tags=["citations"])


@router.get("/{chunk_id}", response_model=CitationRecord)
async def get_citation(
    chunk_id: str,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> CitationRecord:
    """Return the evidence record for `chunk_id` (404 when unknown)."""
    ctx = get_ctx(request)
    if ctx.vector_store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    chunks = await ctx.vector_store.get_by_ids([chunk_id])
    if not chunks:
        raise HTTPException(status_code=404, detail=f"unknown citation: {chunk_id}")
    # CitationRecord is a metadata subset of EvidenceChunk; extra fields
    # (scores, child chunks, ...) are dropped by pydantic.
    return CitationRecord(**chunks[0].model_dump())
