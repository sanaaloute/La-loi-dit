"""Admin router: operational introspection endpoints (spec §49).

All endpoints require the ADMIN role. The read-only introspection endpoints
(audit log, ingestion status, evaluation report, retrieval analytics) are
offline: the audit log is the in-memory ring buffer filled by the middleware,
the ingestion status comes from ``versions.json`` and
``ingestion_results.json``, evaluation results from the JSON report written
by the offline evaluation runner, and retrieval analytics are aggregated from
the same audit log (Prometheus /metrics stays the cross-process source of
truth).

The dashboard endpoints add user/usage administration, provider
introspection (keys always masked) and legal-docs document management
(folders, metadata suggestion, upload, delete) on top of the same ctx.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from backend.api.deps import get_ctx
from backend.core import catalog
from backend.core.models import (
    AuditLogEntry,
    AuditLogResponse,
    AuthorityLevel,
    DocumentIngestResult,
    DocumentType,
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


# ---------------------------------------------------------------------------
# Dashboard: users, usage, providers
# ---------------------------------------------------------------------------


class AdminUserEntry(BaseModel):
    """One account row for the admin users table."""

    id: str
    email: str
    name: str = ""
    role: str = ""
    tier: str = ""
    created_at: str = ""
    today_tokens_in: int = 0
    today_tokens_out: int = 0
    today_requests: int = 0


class AdminUsersResponse(BaseModel):
    users: list[AdminUserEntry]


class AdminUserPatch(BaseModel):
    """Editable account fields (at least one required)."""

    tier: Optional[str] = None
    role: Optional[str] = None


class AdminUsageRow(BaseModel):
    user_id: str
    email: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    requests: int = 0


class AdminUsageResponse(BaseModel):
    per_user: list[AdminUsageRow]
    totals: dict[str, int]


class ProviderModelInfo(BaseModel):
    """One catalog model offered by a provider."""

    id: str
    label: str = ""
    tier_required: str = ""  # lowest tier unlocking it


class ProviderInfo(BaseModel):
    """One configured/configurable provider; keys are ALWAYS masked."""

    provider: str
    configured: bool
    api_base: str = ""
    key_suffix: Optional[str] = None  # "…" + last 4 chars, never the full key
    model: str = ""
    models: list[ProviderModelInfo] = []


class ProvidersResponse(BaseModel):
    providers: list[ProviderInfo]
    defaults: dict[str, str]  # tier -> default catalog model id
    infra: dict[str, Any]  # ctx.infra_status (same source as /ready)


def _user_store(request: Request) -> Any:
    store = getattr(get_ctx(request), "user_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="user store unavailable")
    return store


async def _user_entry(store: Any, record: Any) -> AdminUserEntry:
    today = await store.get_today_usage(record.id)
    return AdminUserEntry(
        id=record.id,
        email=record.email,
        name=record.name,
        role=record.role.value if isinstance(record.role, Role) else str(record.role),
        tier=record.tier,
        created_at=record.created_at,
        today_tokens_in=int(today.get("tokens_in", 0)),
        today_tokens_out=int(today.get("tokens_out", 0)),
        today_requests=int(today.get("requests", 0)),
    )


@router.get("/users", response_model=AdminUsersResponse)
async def list_users(request: Request) -> AdminUsersResponse:
    """All accounts with today's token consumption (newest first)."""
    store = _user_store(request)
    records = await store.list_users()
    return AdminUsersResponse(users=[await _user_entry(store, r) for r in records])


@router.patch("/users/{user_id}", response_model=AdminUserEntry)
async def patch_user(
    user_id: str,
    payload: AdminUserPatch,
    request: Request,
    user: TokenPayload = Depends(require_role(Role.ADMIN)),
) -> AdminUserEntry:
    """Update a user's tier and/or role.

    An admin cannot change their OWN tier/role (lockout protection); unknown
    tiers/roles and unknown users are rejected.
    """
    if payload.tier is None and payload.role is None:
        raise HTTPException(status_code=400, detail="nothing to update: provide tier and/or role")
    if payload.tier is not None and payload.tier not in catalog.TIER_ORDER:
        raise HTTPException(status_code=400, detail=f"unknown tier: {payload.tier}")
    if payload.role is not None:
        try:
            Role(payload.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unknown role: {payload.role}") from None
    if user_id in {user.user_id, user.sub}:
        raise HTTPException(status_code=400, detail="an admin cannot change their own tier/role")

    store = _user_store(request)
    record = await store.get_by_id(user_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"unknown user: {user_id}")
    if payload.tier is not None:
        await store.set_tier(user_id, payload.tier)
    if payload.role is not None and not await store.set_role(user_id, payload.role):
        raise HTTPException(status_code=400, detail=f"unknown role: {payload.role}")
    updated = await store.get_by_id(user_id)
    return await _user_entry(store, updated)


@router.get("/usage", response_model=AdminUsageResponse)
async def usage_overview(
    request: Request,
    days: int = Query(30, ge=1, le=365),
) -> AdminUsageResponse:
    """Per-user token consumption aggregated over the trailing window."""
    store = _user_store(request)
    rows = await store.list_usage(days=days)
    per_user: dict[str, AdminUsageRow] = {}
    for row in rows:
        entry = per_user.setdefault(
            row["user_id"], AdminUsageRow(user_id=row["user_id"], email=row["email"])
        )
        entry.tokens_in += row["tokens_in"]
        entry.tokens_out += row["tokens_out"]
        entry.requests += row["requests"]
    users = sorted(per_user.values(), key=lambda r: -(r.tokens_in + r.tokens_out))
    totals = {
        "tokens_in": sum(r.tokens_in for r in users),
        "tokens_out": sum(r.tokens_out for r in users),
        "requests": sum(r.requests for r in users),
    }
    return AdminUsageResponse(per_user=users, totals=totals)


@router.get("/providers", response_model=ProvidersResponse)
async def providers_overview(request: Request) -> ProvidersResponse:
    """Provider configuration status; API keys are NEVER exposed in full."""
    from backend.core.llm import PROVIDER_DEFAULT_API_BASES
    from backend.core.model_router import _provider_api_key

    ctx = get_ctx(request)
    settings = ctx.settings

    # Catalog models grouped per provider (first tier unlocking each model).
    models_by_provider: dict[str, list[ProviderModelInfo]] = {}
    seen: set[str] = set()
    for tier in catalog.TIER_ORDER:
        for entry in catalog.get_tier(tier, settings=settings).get("models", []):
            if entry["id"] in seen:
                continue
            seen.add(entry["id"])
            models_by_provider.setdefault(entry.get("provider", ""), []).append(
                ProviderModelInfo(
                    id=entry["id"],
                    label=entry.get("label", ""),
                    tier_required=tier,
                )
            )

    providers: list[ProviderInfo] = []
    for provider in catalog._ALL_PROVIDERS:
        # Dedicated key only — no cross-provider fallback inheritance, so
        # "configured" means THIS provider's models are genuinely usable.
        key = _provider_api_key(provider, settings)
        providers.append(
            ProviderInfo(
                provider=provider,
                configured=bool(key),
                api_base=PROVIDER_DEFAULT_API_BASES.get(provider, ""),
                key_suffix=f"…{key[-4:]}" if key else None,
                models=models_by_provider.get(provider, []),
            )
        )
    # Active embedding configuration as a separate entry. No "llm" row: the
    # default chat model already appears per provider above and would only
    # duplicate the catalog information here.
    providers.append(
        ProviderInfo(
            provider="embedding",
            configured=bool(settings.embedding_api_key or settings.llm_api_key),
            api_base=settings.embedding_api_base,
            model=settings.embedding_model,
        )
    )
    defaults = {tier: catalog.default_model(tier, settings=settings) for tier in catalog.TIER_ORDER}
    return ProvidersResponse(
        providers=providers,
        defaults=defaults,
        infra=dict(ctx.infra_status or {}),
    )


# ---------------------------------------------------------------------------
# Dashboard: legal-docs document management
# ---------------------------------------------------------------------------


class FolderInfo(BaseModel):
    name: str
    files: int


class FoldersResponse(BaseModel):
    folders: list[FolderInfo]


class FolderCreateRequest(BaseModel):
    name: str


class FolderCreateResponse(BaseModel):
    name: str  # domain slug actually created
    created: bool  # False when the folder already existed


class MetadataSuggestionResponse(BaseModel):
    suggestion: dict[str, Any]
    available_domains: list[str]


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _legal_docs_dir(request: Request) -> Path:
    """``<data_dir>/legal_docs``, created on demand."""
    ctx = get_ctx(request)
    root = ctx.settings.ensure_data_dir() / "legal_docs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_folder(root: Path, folder: str) -> Path:
    """Resolve `folder` inside `root`, rejecting traversal and unknown folders."""
    candidate = (root / folder).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid folder: {folder}") from None
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail=f"unknown folder: {folder}")
    return candidate


@router.get("/documents/folders", response_model=FoldersResponse)
async def list_folders(request: Request) -> FoldersResponse:
    """Subfolders of ``legal_docs`` with their supported-file counts."""
    from backend.ingestion.pipeline import SUPPORTED_EXTENSIONS

    root = _legal_docs_dir(request)
    folders = [
        FolderInfo(
            name=entry.name,
            files=sum(
                1
                for p in entry.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            ),
        )
        for entry in sorted(root.iterdir())
        if entry.is_dir()
    ]
    return FoldersResponse(folders=folders)


@router.post("/documents/folders", response_model=FolderCreateResponse)
async def create_folder(
    payload: FolderCreateRequest,
    request: Request,
) -> FolderCreateResponse:
    """Create a legal-docs subfolder named by the domain slug of `name`."""
    from backend.ingestion.classification import domain_slug

    raw = payload.name.strip()
    slug = domain_slug(raw)
    # Reject names that slug away to nothing or carry path semantics: the
    # folder name must be exactly the slug, never a path fragment.
    if not slug or "/" in raw or "\\" in raw or ".." in raw:
        raise HTTPException(status_code=400, detail=f"invalid folder name: {payload.name!r}")
    root = _legal_docs_dir(request)
    target = root / slug
    created = not target.exists()
    target.mkdir(parents=True, exist_ok=True)
    return FolderCreateResponse(name=slug, created=created)


@router.post("/documents/metadata-suggestion", response_model=MetadataSuggestionResponse)
async def metadata_suggestion(
    request: Request,
    file: UploadFile = File(...),
) -> MetadataSuggestionResponse:
    """Suggest ingestion metadata for an uploaded file (heuristics + LLM).

    The file is written to a temporary location, the first pages are
    extracted, heuristic classification is merged with the pipeline's
    last-resort LLM classification, and the temp file is ALWAYS deleted.
    Nothing is ingested. An LLM failure degrades to the heuristics-only
    suggestion (still a 200).
    """
    from backend.ingestion.classification import (
        extract_law_number,
        infer_authority,
        infer_document_type,
        infer_legal_domains,
        load_domain_keywords,
    )
    from backend.ingestion.loaders import ExtractedDocument, load_any
    from backend.ingestion.pipeline import IngestionPipeline, _coerce_authority

    ctx = get_ctx(request)
    tmp_dir = ctx.settings.ensure_data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    original_name = Path(file.filename or "document.txt").name
    fd, tmp_name = tempfile.mkstemp(dir=str(tmp_dir), suffix=Path(original_name).suffix)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(await file.read())
        doc = await load_any(tmp_path)
        pages = doc.pages[:3]
        sample_text = "\n\n".join(pages) if pages else doc.text
        sample = ExtractedDocument(
            name=original_name,
            text=sample_text[:6000],
            pages=pages,
            metadata=doc.metadata,
        )
        metadata: dict[str, Any] = {
            "document_name": "",
            "authority": infer_authority(original_name),
            "document_type": infer_document_type(original_name, sample.text),
            "law_number": extract_law_number(original_name),
            "legal_domains": infer_legal_domains(original_name, sample.text),
        }
        # The pipeline's last-resort LLM classification fills the still-empty
        # fields; it swallows LLM/parse failures and returns the heuristics.
        pipeline = IngestionPipeline(ctx)
        enriched = await pipeline._llm_classify_metadata(metadata, sample)

        authority = _coerce_authority(enriched.get("authority"))
        doc_type = enriched.get("document_type")
        if doc_type is not None and not isinstance(doc_type, DocumentType):
            try:
                doc_type = DocumentType(str(doc_type))
            except ValueError:
                doc_type = None
        suggestion = {
            "document_name": enriched.get("document_name") or original_name,
            "authority": "" if authority is AuthorityLevel.UNKNOWN else authority.value,
            "document_type": doc_type.value if isinstance(doc_type, DocumentType) else "",
            "law_number": enriched.get("law_number") or "",
            "legal_domains": [str(d) for d in enriched.get("legal_domains") or []],
            "publication_date": "",
            "effective_date": "",
            "government_body": "",
            "url": "",
        }
        return MetadataSuggestionResponse(
            suggestion=suggestion,
            available_domains=sorted(load_domain_keywords().keys()),
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


@router.post("/documents/upload", response_model=DocumentIngestResult)
async def admin_upload_document(
    request: Request,
    file: UploadFile = File(...),
    folder: str = Form(""),
    metadata: str = Form(""),
) -> DocumentIngestResult:
    """Persist an upload under ``legal_docs/<folder>/`` and ingest it.

    ``metadata`` is an optional JSON object of ingestion metadata (dates and
    authority are coerced like the pipeline's external metadata). On ingest
    failure the written file is removed again.
    """
    from backend.ingestion.pipeline import IngestionPipeline, _coerce_authority, _coerce_date

    root = _legal_docs_dir(request)
    folder = folder.strip().strip("/")
    target_dir = _resolve_folder(root, folder) if folder else root

    safe_name = _SAFE_NAME.sub("_", Path(file.filename or "document").name).lstrip(".")
    if not safe_name or safe_name == "..":
        raise HTTPException(status_code=400, detail=f"invalid filename: {file.filename!r}")

    try:
        meta = json.loads(metadata) if metadata.strip() else {}
    except ValueError:
        raise HTTPException(status_code=400, detail="metadata must be a JSON object") from None
    if not isinstance(meta, dict):
        raise HTTPException(status_code=400, detail="metadata must be a JSON object")
    for key in ("publication_date", "effective_date"):
        if key in meta:
            meta[key] = _coerce_date(meta[key])
    if "authority" in meta:
        meta["authority"] = _coerce_authority(meta["authority"])

    dest = target_dir / safe_name
    dest.write_bytes(await file.read())
    logger.info(
        "admin document uploaded",
        extra={"path": str(dest), "bytes": dest.stat().st_size},
    )

    pipeline = IngestionPipeline(get_ctx(request))
    try:
        return await pipeline.ingest_path(dest, **meta)
    except Exception as exc:
        try:
            dest.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"ingestion failed: {exc}") from exc


@router.delete("/documents/{document_id}", response_model=DocumentIngestResult)
async def admin_delete_document(document_id: str, request: Request) -> DocumentIngestResult:
    """Remove a document from the vector store, keyword index and registry."""
    from backend.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline(get_ctx(request))
    return await pipeline.delete_document(document_id)
