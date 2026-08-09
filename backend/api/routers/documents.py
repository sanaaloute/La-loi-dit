"""Documents router: upload + ingestion trigger, and version/status lookup.

Ingestion imports stay lazy so the router loads even while the subsystem is
unavailable; the status lookup reads the version store directly (same
pattern as ``routers/sources.py``).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from backend.api.deps import get_ctx
from backend.core.models import DocumentIngestResult, ReindexSummary, Role
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@router.post("/reindex", response_model=ReindexSummary)
async def reindex_documents(
    request: Request,
    user: TokenPayload = Depends(require_role(Role.LEGAL_EXPERT)),
) -> ReindexSummary:
    """Re-ingest every document under ``data_dir/legal_docs``.

    Requires at least the LEGAL_EXPERT role (ADMIN included by hierarchy).
    Same target directory as the startup auto-ingest; the pipeline's
    content-hash versioning skips unchanged documents and garbage-collects
    stale ones. Awaited like the upload endpoint: the pipeline is fully
    async, so the event loop stays responsive while it runs.
    """
    from pathlib import Path

    ctx = get_ctx(request)
    target = Path(ctx.settings.data_dir) / "legal_docs"
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"legal docs directory not found: {target}")

    try:
        from backend.ingestion.pipeline import IngestionPipeline
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="ingestion subsystem unavailable") from exc

    logger.info("manual reindex triggered", extra={"directory": str(target), "user": user.sub})
    pipeline = IngestionPipeline(ctx)
    results = await pipeline.reindex_directory(target)

    summary = ReindexSummary(directory=str(target), scanned=len(results))
    for r in results:
        if r.status == "indexed":
            summary.indexed += 1
        elif r.status == "skipped_duplicate":
            summary.skipped_duplicate += 1
        elif r.status == "failed":
            summary.failed += 1
        elif r.status == "deleted":
            summary.deleted += 1
        summary.chunks_created += r.chunks_created
    logger.info("manual reindex done", extra=summary.model_dump())
    return summary



@router.post("", response_model=DocumentIngestResult)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    language: str = Form("fr"),
    user: TokenPayload = Depends(require_role(Role.LEGAL_EXPERT)),
) -> DocumentIngestResult:
    """Upload a document, persist it under data_dir/uploads and ingest it.

    Requires at least the LEGAL_EXPERT role.
    """
    ctx = get_ctx(request)
    settings = ctx.settings

    upload_dir = settings.ensure_data_dir() / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)

    document_id = uuid.uuid4().hex
    safe_name = _SAFE_NAME.sub("_", file.filename or "document")
    dest = upload_dir / f"{document_id}_{safe_name}"
    content = await file.read()
    dest.write_bytes(content)
    logger.info(
        "document uploaded",
        extra={"document_id": document_id, "filename": safe_name, "bytes": len(content), "user": user.sub},
    )

    try:
        from backend.ingestion.pipeline import IngestionPipeline
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="ingestion subsystem unavailable") from exc

    metadata: dict[str, Any] = {
        "document_id": document_id,
        "title": title or safe_name,
        "language": language,
        "uploaded_by": user.sub,
    }
    pipeline = IngestionPipeline(ctx)
    ingest = getattr(pipeline, "ingest_file", None) or getattr(pipeline, "ingest", None)
    if ingest is None:
        raise HTTPException(status_code=503, detail="ingestion pipeline has no ingest entrypoint")

    try:
        result = await ingest(dest, metadata=metadata)
    except TypeError:  # pipeline without metadata kwarg
        result = await ingest(dest)

    if isinstance(result, DocumentIngestResult):
        return result
    if isinstance(result, dict):
        return DocumentIngestResult(**result)
    # Unknown return shape: report a best-effort success.
    return DocumentIngestResult(
        document_id=document_id,
        document_name=metadata["title"],
        chunks_created=int(getattr(result, "chunks_created", 0) or 0),
        version=int(getattr(result, "version", 1) or 1),
        status="indexed",
    )


@router.get("/{document_id}")
async def document_status(
    document_id: str,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.VIEWER)),
) -> dict[str, Any]:
    """Return version/status info for a document (404 when unknown).

    Combines the version store (``versions.json`` — version, content hash,
    per-article hashes, read via ``VersionStore._load()`` like
    ``routers/sources.py`` does) with the document's chunks in the vector
    store (name, chunk count).  A document is "known" when either side has
    it.  The latest persisted ingestion record (``ingestion_results.json``)
    is included when present.
    """
    from backend.ingestion.pipeline import load_ingestion_results
    from backend.ingestion.versioning import VersionStore

    ctx = get_ctx(request)

    state = VersionStore(ctx.settings.data_dir)._load()
    entry = state.get(document_id)

    chunks = []
    if ctx.vector_store is not None:
        chunks = await ctx.vector_store.get_by_document_id(document_id)

    if entry is None and not chunks:
        raise HTTPException(status_code=404, detail=f"unknown document: {document_id}")

    info: dict[str, Any] = {
        "document_id": document_id,
        "document_name": chunks[0].document_name if chunks else "",
        "version": 1,
        "content_hash": "",
        "article_count": 0,
        "chunk_count": len(chunks),
    }
    if entry is not None:
        info["version"] = int(entry.get("version", 1))
        info["content_hash"] = str(entry.get("hash", ""))
        info["article_count"] = len(entry.get("articles") or {})

    latest = load_ingestion_results(ctx.settings.data_dir).get(document_id)
    if latest is not None:
        info["ingestion"] = latest
    return info
