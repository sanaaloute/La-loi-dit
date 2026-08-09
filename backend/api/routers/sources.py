"""Sources router: document-level source record lookup (spec §48).

Combines the ingestion version store (``versions.json`` — version, content
hash, per-article hashes) with the document metadata carried by its chunks
in the vector store (authority, document type, law number, dates, url).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.api.deps import get_ctx
from backend.core.models import Role, SourceRecord
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("/{document_id}", response_model=SourceRecord)
async def get_source(
    document_id: str,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> SourceRecord:
    """Return the source record for `document_id` (404 when unknown).

    A document is "known" when the version store tracks it or the vector
    store still holds at least one of its chunks.
    """
    from backend.ingestion.versioning import VersionStore

    ctx = get_ctx(request)

    state = VersionStore(ctx.settings.data_dir)._load()
    entry = state.get(document_id)

    chunks = []
    if ctx.vector_store is not None:
        chunks = await ctx.vector_store.get_by_document_id(document_id)

    if entry is None and not chunks:
        raise HTTPException(status_code=404, detail=f"unknown source: {document_id}")

    record = SourceRecord(document_id=document_id, chunk_count=len(chunks))
    if entry is not None:
        record.version = int(entry.get("version", 1))
        record.content_hash = str(entry.get("hash", ""))
        record.article_count = len(entry.get("articles") or {})
    if chunks:
        # Chunks of one document share its document-level metadata; take the
        # first as representative.
        first = chunks[0]
        record.document_name = first.document_name
        record.authority = first.authority
        record.document_type = first.document_type
        record.law_number = first.law_number
        record.status = first.status
        record.publication_date = first.publication_date
        record.effective_date = first.effective_date
        record.url = first.url
        record.language = first.language
    return record
