"""Admin router: operational introspection endpoints (spec §49).

All endpoints require the ADMIN role. Everything is read-only and offline:
the audit log is the in-memory ring buffer filled by the middleware, the
ingestion status comes from ``versions.json`` and ``ingestion_results.json``,
evaluation results from the JSON report written by the offline evaluation
runner, and retrieval analytics are aggregated from the same audit log
(Prometheus /metrics stays the cross-process source of truth).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from backend.api.deps import get_ctx
from backend.core.models import (
    AuditLogEntry,
    AuditLogResponse,
    EndpointStats,
    EvaluationLatestResponse,
    IngestionDocumentStatus,
    IngestionStatusResponse,
    RetrievalAnalyticsResponse,
    Role,
    UserRequestStats,
)
from backend.security.jwt import TokenPayload
from backend.security.rbac import require_role

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)

_EVAL_REPORT = Path("eval") / "eval_report.json"


def _audit_entries(request: Request) -> list[dict[str, Any]]:
    buf = getattr(request.app.state, "audit_log", None)
    return list(buf) if buf is not None else []


@router.get("/audit-log", response_model=AuditLogResponse)
async def audit_log(
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
) -> AuditLogResponse:
    """Return the most recent audit entries (newest first)."""
    buf = getattr(request.app.state, "audit_log", None)
    entries = list(buf) if buf is not None else []
    cap = int(getattr(buf, "maxlen", 0) or 0)
    recent = [AuditLogEntry(**e) for e in reversed(entries[-limit:])]
    return AuditLogResponse(entries=recent, count=len(recent), cap=cap)


@router.get("/ingestion/status", response_model=IngestionStatusResponse)
async def ingestion_status(request: Request) -> IngestionStatusResponse:
    """Per-document version/hash/article counts from the version store.

    ``failed_documents`` is filled from ``ingestion_results.json`` — the
    latest persisted record per document (see ``note`` in the response).
    """
    from backend.ingestion.pipeline import load_ingestion_results
    from backend.ingestion.versioning import VersionStore

    ctx = get_ctx(request)
    store = VersionStore(ctx.settings.data_dir)
    state = store._load()
    documents = [
        IngestionDocumentStatus(
            document_id=document_id,
            version=int(entry.get("version", 1)),
            content_hash=str(entry.get("hash", "")),
            article_count=len(entry.get("articles") or {}),
        )
        for document_id, entry in sorted(state.items())
    ]
    failed = sorted(
        (
            record
            for record in load_ingestion_results(ctx.settings.data_dir).values()
            if record.get("status") == "failed"
        ),
        key=lambda record: str(record.get("document_id", "")),
    )
    updated_at = None
    try:
        mtime = (Path(ctx.settings.data_dir) / "versions.json").stat().st_mtime
        updated_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
    except OSError:
        pass
    return IngestionStatusResponse(
        documents=documents,
        total_documents=len(documents),
        store_updated_at=updated_at,
        failed_documents=failed,
    )


@router.get("/evaluation/latest", response_model=EvaluationLatestResponse)
async def evaluation_latest(request: Request) -> EvaluationLatestResponse:
    """Return the latest offline evaluation report (404 when none exists)."""
    ctx = get_ctx(request)
    path = Path(ctx.settings.data_dir) / _EVAL_REPORT
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise HTTPException(status_code=404, detail="no evaluation report found") from None
    except ValueError:
        raise HTTPException(status_code=500, detail=f"corrupt evaluation report: {path}") from None
    if not isinstance(report, dict):
        raise HTTPException(status_code=500, detail=f"corrupt evaluation report: {path}")
    return EvaluationLatestResponse(
        path=str(path),
        generated_at=report.get("generated_at"),
        dataset=report.get("dataset"),
        total_cases=report.get("total_cases"),
        pass_rate=report.get("pass_rate"),
        report=report,
    )


@router.get("/retrieval/analytics", response_model=RetrievalAnalyticsResponse)
async def retrieval_analytics(request: Request) -> RetrievalAnalyticsResponse:
    """Aggregate request counters from the in-memory audit log.

    Honest subset of "retrieval analytics": per-path request/error counts
    and average latency, plus per-user-subject counts. Role-level and
    cross-process numbers are not available from this source (see `note`).
    """
    entries = _audit_entries(request)
    paths: dict[str, dict[str, float]] = {}
    users: dict[str, int] = {}
    for e in entries:
        stat = paths.setdefault(e["path"], {"requests": 0, "errors": 0, "latency": 0.0})
        stat["requests"] += 1
        stat["errors"] += 1 if int(e["status"]) >= 500 else 0
        stat["latency"] += float(e["latency_ms"])
        users[e["user"]] = users.get(e["user"], 0) + 1
    by_path = [
        EndpointStats(
            path=path,
            requests=int(s["requests"]),
            errors=int(s["errors"]),
            avg_latency_ms=round(s["latency"] / s["requests"], 1),
        )
        for path, s in sorted(paths.items(), key=lambda kv: -kv[1]["requests"])
    ]
    by_user = [
        UserRequestStats(user=user, requests=count)
        for user, count in sorted(users.items(), key=lambda kv: -kv[1])
    ]
    return RetrievalAnalyticsResponse(
        total_requests=len(entries),
        by_path=by_path,
        by_user=by_user,
    )
