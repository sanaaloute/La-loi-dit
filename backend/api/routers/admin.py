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
# Dashboard: users, usage, tier budgets, providers
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

    # Enforce exactly one admin account.
    if payload.role == Role.ADMIN.value and record.role != Role.ADMIN:
        if await store.has_admin():
            raise HTTPException(status_code=400, detail="an admin account already exists")

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


class TierBudgetPatch(BaseModel):
    """Daily budget fields adjustable per tier (all optional)."""

    daily_token_budget: Optional[int] = None
    daily_request_budget: Optional[int] = None


class TierBudgetsResponse(BaseModel):
    """Per-tier daily budgets: effective values and built-in defaults."""

    effective: dict[str, dict[str, int]]
    defaults: dict[str, dict[str, int]]


async def _stored_budget_overrides(store: Any) -> dict[str, dict[str, int]]:
    """Persisted budget overrides (validated; {} when unset/unusable)."""
    raw = await store.get_setting(catalog.TIER_BUDGETS_SETTING_KEY)
    return catalog.parse_budget_overrides(raw)


def _tier_budgets_response(request: Request) -> TierBudgetsResponse:
    return TierBudgetsResponse(
        effective=catalog.effective_tier_budgets(settings=get_ctx(request).settings),
        defaults=catalog.default_tier_budgets(),
    )


@router.get("/settings/tier-budgets", response_model=TierBudgetsResponse)
async def get_tier_budgets(request: Request) -> TierBudgetsResponse:
    """Effective per-tier daily budgets (built-in defaults + admin overrides).

    The persisted overrides are (re)loaded into the catalog cache on every
    read, so a restart picks them up even before the next write.
    """
    store = _user_store(request)
    catalog.set_budget_overrides(await _stored_budget_overrides(store))
    return _tier_budgets_response(request)


@router.patch("/settings/tier-budgets", response_model=TierBudgetsResponse)
async def patch_tier_budgets(
    payload: dict[str, TierBudgetPatch],
    request: Request,
) -> TierBudgetsResponse:
    """Merge admin budget overrides, persist them and refresh the catalog.

    Unknown tiers and non-positive values are rejected; omitted fields keep
    their current (overridden or default) value.
    """
    if not payload:
        raise HTTPException(status_code=400, detail="nothing to update: provide at least one tier")
    for tier, fields in payload.items():
        if tier not in catalog.TIER_ORDER:
            raise HTTPException(status_code=400, detail=f"unknown tier: {tier}")
        for field, value in fields.model_dump(exclude_none=True).items():
            if value <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"{field} for tier '{tier}' must be a positive integer",
                )

    store = _user_store(request)
    overrides = await _stored_budget_overrides(store)
    for tier, fields in payload.items():
        updates = fields.model_dump(exclude_none=True)
        if updates:
            overrides.setdefault(tier, {}).update(updates)
    await store.set_setting(catalog.TIER_BUDGETS_SETTING_KEY, json.dumps(overrides))
    catalog.set_budget_overrides(overrides)
    return _tier_budgets_response(request)


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
    domain_labels: dict[str, str]  # slug -> French display label


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

#: Headroom for multipart framing (boundaries, part headers) when the early
#: Content-Length check compares the whole request body to the file cap.
_MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _legal_docs_dir(request: Request) -> Path:
    """``<data_dir>/legal_docs``, created on demand.

    Raises a clear HTTP 500 when the directory cannot be created or read so
    that permission/volume problems are visible in the admin UI.
    """
    ctx = get_ctx(request)
    data_dir = ctx.settings.ensure_data_dir()
    root = data_dir / "legal_docs"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error(
            "cannot create legal_docs directory",
            extra={"data_dir": str(data_dir), "legal_docs": str(root), "error": str(exc)},
        )
        raise HTTPException(
            status_code=500,
            detail=f"cannot create legal_docs directory: {exc}",
        ) from exc
    if not root.is_dir():
        detail = f"legal_docs path is not a directory: {root}"
        logger.error(detail, extra={"data_dir": str(data_dir)})
        raise HTTPException(status_code=500, detail=detail)
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
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        logger.error(
            "cannot read legal_docs directory",
            extra={"legal_docs": str(root), "error": str(exc)},
        )
        raise HTTPException(
            status_code=500,
            detail=f"cannot read legal_docs directory: {exc}",
        ) from exc

    folders: list[FolderInfo] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            file_count = sum(
                1
                for p in entry.iterdir()
                if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
            )
        except OSError:
            # A single unreadable subfolder should not break the whole listing.
            file_count = 0
        folders.append(FolderInfo(name=entry.name, files=file_count))
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


# ---------------------------------------------------------------------------
# Dashboard: legal-domain taxonomy management
# ---------------------------------------------------------------------------


class DomainInfo(BaseModel):
    """One legal-domain taxonomy entry (slug, French label, keyword stems)."""

    slug: str
    label: str
    keywords: list[str]


class DomainsResponse(BaseModel):
    domains: list[DomainInfo]


class DomainCreateRequest(BaseModel):
    slug: str
    label: str
    keywords: list[str] = []


_DOMAIN_SLUG_RE = re.compile(r"^[a-z0-9_]+$")


def _list_domains() -> list[DomainInfo]:
    """Effective taxonomy (file or embedded fallback), sorted by slug."""
    from backend.ingestion.classification import load_domain_keywords, load_domain_labels

    keywords = load_domain_keywords()
    labels = load_domain_labels()
    return [
        DomainInfo(slug=slug, label=labels.get(slug, slug), keywords=kws)
        for slug, kws in sorted(keywords.items())
    ]


def _read_domains_file(path: Path) -> dict[str, Any]:
    """Raw ``domains`` object of the taxonomy file, for rewriting.

    A missing/corrupt file seeds from the effective taxonomy (embedded
    fallback included) so a write never silently drops the active domains.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("domains") if isinstance(data, dict) else None
        if isinstance(raw, dict):
            return dict(raw)
    except Exception:  # missing/corrupt: fall through to the effective taxonomy
        pass
    return {
        info.slug: {"label": info.label, "keywords": info.keywords} for info in _list_domains()
    }


def _write_domains_file(path: Path, domains: dict[str, Any]) -> None:
    """Persist the taxonomy atomically (temp file + replace), then invalidate."""
    from backend.ingestion.classification import invalidate_domain_cache

    payload = json.dumps({"version": 1, "domains": domains}, ensure_ascii=False, indent=1) + "\n"
    tmp_name = ""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except OSError as exc:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        logger.error(
            "cannot write legal domains file",
            extra={"path": str(path), "error": str(exc)},
        )
        raise HTTPException(
            status_code=500, detail=f"cannot write legal domains file: {exc}"
        ) from exc
    invalidate_domain_cache()


def _domains_write_path(request: Request) -> Path:
    """The file ``load_domain_keywords`` resolves for this app's settings."""
    from backend.ingestion.classification import resolve_domains_path

    return resolve_domains_path(getattr(get_ctx(request).settings, "legal_domains_path", None))


@router.get("/domains", response_model=DomainsResponse)
async def list_domains() -> DomainsResponse:
    """Legal-domain taxonomy (slug, French label, keywords), sorted by slug."""
    return DomainsResponse(domains=_list_domains())


@router.post("/domains", response_model=DomainInfo)
async def create_domain(payload: DomainCreateRequest, request: Request) -> DomainInfo:
    """Add a legal domain to the taxonomy (persisted, cache invalidated)."""
    slug = payload.slug.strip()
    label = payload.label.strip()
    if not _DOMAIN_SLUG_RE.match(slug):
        raise HTTPException(status_code=400, detail=f"invalid domain slug: {payload.slug!r}")
    if not label:
        raise HTTPException(status_code=400, detail="label is required")
    keywords = [str(kw).strip() for kw in payload.keywords if str(kw).strip()]

    path = _domains_write_path(request)
    domains = _read_domains_file(path)
    if slug in domains:
        raise HTTPException(status_code=409, detail=f"domain already exists: {slug}")
    domains[slug] = {"label": label, "keywords": keywords}
    _write_domains_file(path, domains)
    return DomainInfo(slug=slug, label=label, keywords=keywords)


@router.delete("/domains/{slug}", response_model=DomainsResponse)
async def delete_domain(slug: str, request: Request) -> DomainsResponse:
    """Remove a legal domain; refused while a legal_docs folder with that name
    still holds documents."""
    from backend.ingestion.pipeline import SUPPORTED_EXTENSIONS

    path = _domains_write_path(request)
    domains = _read_domains_file(path)
    if slug not in domains:
        raise HTTPException(status_code=404, detail=f"unknown domain: {slug}")

    folder = get_ctx(request).settings.ensure_data_dir() / "legal_docs" / slug
    try:
        has_documents = folder.is_dir() and any(
            p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS for p in folder.iterdir()
        )
    except OSError:
        has_documents = False
    if has_documents:
        raise HTTPException(
            status_code=409,
            detail=f"domain '{slug}' still has documents in legal_docs/{slug}",
        )

    del domains[slug]
    _write_domains_file(path, domains)
    return DomainsResponse(domains=_list_domains())


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
        load_domain_labels,
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
            domain_labels=load_domain_labels(),
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

    ctx = get_ctx(request)
    max_bytes = ctx.settings.max_upload_bytes_admin
    too_large = f"Fichier trop volumineux (max {max_bytes // (1024 * 1024)} Mo)"
    # Cheap early reject: the multipart body is already over the cap. Allow a
    # small overhead for the multipart framing itself (boundaries, headers);
    # the post-read check on the actual file bytes below stays exact.
    content_length = request.headers.get("content-length")
    if (
        content_length
        and content_length.isdigit()
        and int(content_length) > max_bytes + _MULTIPART_OVERHEAD_BYTES
    ):
        raise HTTPException(status_code=413, detail=too_large)

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
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=too_large)
    dest.write_bytes(content)
    logger.info(
        "admin document uploaded",
        extra={"path": str(dest), "bytes": dest.stat().st_size},
    )

    pipeline = IngestionPipeline(ctx)
    try:
        return await pipeline.ingest_path(dest, **meta)
    except Exception as exc:
        logger.exception(
            "admin ingestion failed",
            extra={"path": str(dest), "metadata": meta},
        )
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
