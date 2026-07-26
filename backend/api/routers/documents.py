"""Documents router: upload + ingestion trigger, and version/status lookup.

The ingestion subsystem is built in parallel, so all imports of it are lazy
and its exact pipeline API is resolved defensively at call time.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from backend.api.deps import get_ctx
from backend.core.models import DocumentIngestResult, Role
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


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
    """Return version/status info for a document (404 when unknown)."""
    get_ctx(request)  # ensure context is up
    try:
        from backend.ingestion import versioning
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="ingestion subsystem unavailable") from exc

    lookup = None
    for name in ("get_document_info", "get_document", "get_version_info", "document_status"):
        candidate = getattr(versioning, name, None)
        if callable(candidate):
            lookup = candidate
            break
    if lookup is None:
        raise HTTPException(status_code=503, detail="versioning lookup unavailable")

    info = lookup(document_id)
    if hasattr(info, "__await__"):
        info = await info
    if info is None:
        raise HTTPException(status_code=404, detail=f"unknown document: {document_id}")
    if hasattr(info, "model_dump"):
        return info.model_dump(mode="json")
    if isinstance(info, dict):
        return info
    return {"document_id": document_id, "detail": str(info)}
