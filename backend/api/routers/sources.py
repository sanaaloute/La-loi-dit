"""Sources router: document-level source record lookup (spec §48).

Combines the ingestion version store (``versions.json`` — version, content
hash, per-article hashes) with the document metadata carried by its chunks
in the vector store (authority, document type, law number, dates, url).

Also powers the corpus browser: ``GET /sources`` lists every indexed
document and ``GET /sources/{id}/articles`` is its article index.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.api.deps import get_ctx
from backend.core.models import Role, SourceRecord
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

router = APIRouter(prefix="/sources", tags=["sources"])


class SourceListItem(BaseModel):
    document_id: str
    document_name: str
    version: int = 1
    chunk_count: int = 0
    folder: str = ""  # bf | ohada | uemoa | cima | …
    status: str = ""
    authority: str = ""
    document_type: str = ""
    law_number: str = ""
    publication_date: str = ""
    legal_domains: list[str] = []


class ArticleIndexEntry(BaseModel):
    article: str
    section: str = ""
    page: int | None = None
    preview: str = ""  # first ~120 chars of the article text


@router.get("", response_model=list[SourceListItem])
async def list_sources(
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> list[SourceListItem]:
    """List every indexed document for the corpus browser.

    Built from the ingestion journal + versions store + the metadata manifest
    (no vector-store queries — safe to call on every page load).
    """
    import json

    from backend.ingestion.versioning import VersionStore

    ctx = get_ctx(request)
    data_dir = ctx.settings.data_dir

    versions = VersionStore(data_dir)._load()
    try:
        journal = json.loads((data_dir / "ingestion_results.json").read_text(encoding="utf-8"))
    except OSError:
        journal = {}
    try:
        manifest = json.loads((data_dir / "legal_sources.json").read_text(encoding="utf-8"))
    except OSError:
        manifest = {}
    doc_meta = manifest.get("document_metadata", {})

    items: list[SourceListItem] = []
    for document_id, ventry in versions.items():
        jentry = journal.get(document_id) or {}
        path = jentry.get("path", "")
        folder = ""
        if "/legal_docs/" in path:
            parts = path.split("/legal_docs/", 1)[1].split("/")
            if len(parts) > 1:
                folder = parts[0]
        meta = doc_meta.get(path.rsplit("/", 1)[-1], {}) if path else {}
        name = jentry.get("document_name") or meta.get("document_name") or document_id
        items.append(
            SourceListItem(
                document_id=document_id,
                document_name=name,
                version=int(ventry.get("version", 1)),
                chunk_count=int(jentry.get("chunks_created") or 0),
                folder=folder,
                authority=str(meta.get("authority", "")),
                document_type=str(meta.get("document_type", "")),
                law_number=str(meta.get("law_number", "")),
                publication_date=str(meta.get("publication_date", "")),
                legal_domains=list(meta.get("legal_domains") or []),
            )
        )
    items.sort(key=lambda i: (i.folder, i.document_name.lower()))
    return items


@router.get("/{document_id}/articles", response_model=list[ArticleIndexEntry])
async def list_articles(
    document_id: str,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> list[ArticleIndexEntry]:
    """Article index of one document (number, section, short preview)."""
    ctx = get_ctx(request)
    if ctx.vector_store is None:
        raise HTTPException(status_code=503, detail="vector store unavailable")
    chunks = await ctx.vector_store.get_by_document_id(document_id)
    if not chunks:
        raise HTTPException(status_code=404, detail=f"unknown source: {document_id}")

    # Parent chunks carry the whole article; children (alinéas) share its
    # article number, so grouping by article and preferring parents gives the
    # index. Chunks without an article number are skipped (preambles etc.).
    by_article: dict[str, ArticleIndexEntry] = {}
    for chunk in chunks:
        if not chunk.article:
            continue
        key = str(chunk.article)
        preview = (chunk.content or "").strip().replace("\n", " ")[:120]
        existing = by_article.get(key)
        entry = ArticleIndexEntry(
            article=key,
            section=chunk.section or (existing.section if existing else ""),
            page=chunk.page if chunk.page is not None else (existing.page if existing else None),
            preview=preview if (existing is None or chunk.parent_chunk_id is None) else existing.preview,
        )
        # Prefer the parent chunk's preview/section (full article text).
        if existing is not None and chunk.parent_chunk_id is not None:
            entry = existing
        by_article[key] = entry

    def _sort_key(entry: ArticleIndexEntry):
        try:
            return (0, int(entry.article))
        except ValueError:
            return (1, entry.article)

    return sorted(by_article.values(), key=_sort_key)


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
