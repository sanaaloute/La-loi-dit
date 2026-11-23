"""Articles router: direct chunk lookup by document + article number.

Goes straight to the vector store (no similarity search): all chunks of the
document are fetched by ``document_id`` and narrowed down with the same
metadata-filter semantics used by retrieval (``matches_filters`` — exact,
enum-safe match on the chunk's ``article`` field/metadata).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.deps import get_ctx
from backend.core.models import ArticleChunk, ArticleLookupResponse, Role
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role
from backend.vectorstore.memory_store import matches_filters

router = APIRouter(prefix="/articles", tags=["articles"])


@router.get("/{document_id}/{article}", response_model=ArticleLookupResponse)
async def get_article(
    document_id: str,
    article: str,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> ArticleLookupResponse:
    """Return every chunk of `document_id` tagged with `article` (404 if none)."""
    ctx = get_ctx(request)
    if ctx.vector_store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")

    chunks = await ctx.vector_store.get_by_document_id(document_id)
    matched = [c for c in chunks if matches_filters(c, {"article": article})]
    if not matched:
        raise HTTPException(
            status_code=404,
            detail=f"no chunks found for document '{document_id}', article '{article}'",
        )

    # Milvus returns chunks in arbitrary order; the reader needs ingestion
    # order (``metadata["seq"]``), e.g. for amending laws that quote an
    # article's old wording then its new one under the same number.
    def _order(chunk) -> tuple:
        seq = (chunk.metadata or {}).get("seq")
        if isinstance(seq, int):
            return (0, seq)
        return (1, chunk.page if chunk.page is not None else -1)

    matched.sort(key=_order)

    # Children are retrieval excerpts of their parent (whitespace-normalized,
    # so the client's exact-substring dedup cannot catch them); showing both
    # duplicates the article in the reader. Drop a child when its parent is
    # in the result; an orphan child stays so the article is never empty.
    parent_ids = {
        c.chunk_id for c in matched if (c.metadata or {}).get("role") == "parent"
    }
    matched = [
        c
        for c in matched
        if (c.metadata or {}).get("role") != "child"
        or c.parent_chunk_id not in parent_ids
    ]

    return ArticleLookupResponse(
        document_id=document_id,
        article=article,
        count=len(matched),
        chunks=[
            ArticleChunk(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                document_name=c.document_name,
                content=c.content,
                article=c.article,
                section=c.section,
                page=c.page,
                publication_date=c.publication_date,
                effective_date=c.effective_date,
                url=c.url,
                authority=c.authority,
                metadata=c.metadata,
            )
            for c in matched
        ],
    )
