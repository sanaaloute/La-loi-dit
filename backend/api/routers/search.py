"""Search router: direct hybrid (vector + keyword) retrieval over the corpus."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.deps import get_ctx
from backend.core.models import Role, SearchKind, SearchTask
from backend.observability import metrics
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

router = APIRouter(tags=["search"])


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=1),
    top_k: int = Query(8, ge=1, le=50),
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    """Run one vector + one keyword search task and return evidence chunks."""
    ctx = get_ctx(request)
    if ctx.retriever is None:
        raise HTTPException(status_code=503, detail="retriever unavailable")

    tasks = [
        SearchTask(kind=SearchKind.VECTOR, query=q, top_k=top_k),
        SearchTask(kind=SearchKind.KEYWORD, query=q, top_k=top_k),
    ]
    with metrics.time_histogram(metrics.retrieval_latency_seconds):
        chunks = await ctx.retriever.retrieve(tasks)

    if ctx.user_store is not None:
        await ctx.user_store.record_prompt(
            user.sub if user.sub != "anonymous" else user.user_id or "anonymous",
            q,
            source="search",
        )

    return {
        "query": q,
        "count": len(chunks),
        "results": [c.model_dump(mode="json") for c in chunks],
    }
