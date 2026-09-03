"""FastAPI application factory for the Burkina Faso Legal AI platform.

`create_app()` wires logging/metrics/tracing, middleware, exception handlers
and the /api/v1 routers. The heavy lifting (AppContext, compiled LangGraph
workflow, dev user store) happens in the lifespan, so importing this module
never boots external subsystems.

Run directly with:  python -m backend.api.main
"""

from __future__ import annotations

import logging
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from backend.core.config import get_settings
from backend.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    GuardrailViolation,
    LegalAIError,
    QuotaExceededError,
    RateLimitError,
    UserAlreadyExistsError,
)
from backend.observability.logging import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot the AppContext and compile the workflow graph once per process."""
    from backend.core.context import build_context
    from backend.workflows.graph import build_graph

    ctx = await build_context()
    app.state.ctx = ctx
    app.state.graph = build_graph(ctx)

    # Load persisted admin tier-budget overrides into the catalog cache so
    # enforcement matches the stored settings from the first request.
    if ctx.user_store is not None:
        try:
            from backend.core import catalog

            raw = await ctx.user_store.get_setting(catalog.TIER_BUDGETS_SETTING_KEY)
            catalog.set_budget_overrides(catalog.parse_budget_overrides(raw))
        except Exception:
            logger.warning("could not load tier budget overrides", exc_info=True)

    from backend.api.routers.auth import build_user_store

    app.state.user_store = build_user_store(ctx.settings)
    if ctx.settings.is_production:
        has_bootstrap_admin = any(
            u["role"].value == "admin" for u in app.state.user_store.values()
        )
        if not has_bootstrap_admin:
            logger.warning(
                "production boot: no admin account configured. Set LEGAL_AI_DEV_USERS="
                "'admin:STRONG_PASSWORD:admin' to bootstrap the single admin account."
            )
        if ctx.settings.secret_key in ("", "change-me-in-production"):
            logger.warning("production boot: LEGAL_AI_SECRET_KEY is using the default value")
    app.state.langfuse = None
    try:
        from backend.observability.langfuse_client import get_langfuse

        app.state.langfuse = get_langfuse(ctx.settings)
    except Exception:
        pass
    if ctx.settings.ingest_on_startup:
        # Background, single-run-guarded: safe with multiple uvicorn workers.
        asyncio.create_task(_auto_ingest_on_startup(app, ctx.settings))
    # Milvus fallback is sticky at boot: probe and swap back without a restart.
    reconnect_task: Optional[asyncio.Task] = None
    if ctx.settings.milvus_enabled:
        reconnect_task = asyncio.create_task(_vector_store_reconnect_loop(ctx))
    # Freshness polling: official sources are checked on a daily loop; each
    # change is persisted to data/freshness_events.jsonl for the Nouveautés feed.
    freshness_task: Optional[asyncio.Task] = None
    if ctx.settings.freshness_check_enabled:
        freshness_task = asyncio.create_task(_freshness_loop(ctx))
    logger.info("application started", extra={"env": ctx.settings.env})
    yield
    for task in (reconnect_task, freshness_task):
        if task is not None:
            task.cancel()
    app.state.graph = None
    app.state.ctx = None


async def _freshness_loop(ctx: Any) -> None:
    """Daily (configurable) poll of the freshness registry.

    Runs in every uvicorn worker; the monitor's seen-state file makes repeat
    detections idempotent, and event appends are small enough that duplicate
    worker writes just produce a duplicated line readers tolerate.
    """
    from backend.ingestion.freshness import FreshnessMonitor, append_event

    async def _record(event: Any) -> None:
        append_event(ctx.settings.data_dir, event)
        logger.info("freshness change: %s (%s)", event.source_name, event.url)
        from backend.core.push import send_push

        await send_push(
            ctx,
            f"Nouveauté juridique — {event.source_name}",
            event.detail or event.url,
            {"url": event.url},
        )

    monitor = FreshnessMonitor(ctx, on_change=_record)
    await asyncio.sleep(120)  # let the app finish warming up first
    while True:
        try:
            await monitor.check_sources()
        except Exception:
            logger.warning("freshness check failed", exc_info=True)
        await asyncio.sleep(max(3600.0, ctx.settings.freshness_interval_hours * 3600))


async def _vector_store_reconnect_loop(ctx: Any) -> None:
    """Swap the in-memory fallback for Milvus as soon as it is reachable.

    The factory downgrades silently when Milvus is unreachable at boot
    (slow service startup, NAT flap); without this probe the process would
    serve degraded retrieval until the next deploy. All consumers read
    ``ctx.vector_store`` dynamically, so swapping the attribute is enough.
    Runs per uvicorn worker — each process fixes its own ctx.
    """
    from backend.vectorstore.factory import get_vector_store
    from backend.vectorstore.memory_store import InMemoryVectorStore

    while True:
        await asyncio.sleep(60)
        if not isinstance(ctx.vector_store, InMemoryVectorStore):
            continue
        try:
            store = await get_vector_store(ctx.settings)
        except Exception:
            continue  # never crash the probe loop
        if isinstance(store, InMemoryVectorStore):
            continue
        ctx.vector_store = store
        ctx.infra_status["milvus"] = "ok"
        ctx.infra_status["vector_store_probe"] = "ok"
        logger.info("vector store: Milvus reconnected (fallback swapped out)")


async def _auto_ingest_on_startup(app: FastAPI, settings: Any) -> None:
    """Index ``data/legal_docs`` on boot when LEGAL_AI_INGEST_ON_STARTUP=true.

    Idempotent by design: the pipeline's content-hash versioning skips
    unchanged documents, so a boot with no document changes costs one scan.
    The shared ingestion lock guards against concurrent runs when uvicorn
    spawns several workers and against CLI reindexes running in a separate
    container (only the first runner ingests).
    """
    from pathlib import Path

    from backend.ingestion.ingest_lock import ingestion_lock

    with ingestion_lock(settings.data_dir) as acquired:
        if not acquired:
            logger.info("startup ingestion: skipped (another run holds the lock)")
            return
        try:
            target = Path(settings.data_dir) / "legal_docs"
            if not target.exists():
                logger.info("startup ingestion: %s not found, nothing to index", target)
                return
            from backend.ingestion.pipeline import IngestionPipeline

            pipeline = IngestionPipeline(app.state.ctx)
            results = await pipeline.reindex_directory(target)
            summary: dict[str, int] = {}
            for r in results:
                summary[r.status] = summary.get(r.status, 0) + 1
            logger.info("startup ingestion done: %s", summary)
        except Exception:
            logger.exception("startup ingestion failed")


def _error_handler(status_code: int):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        from backend.observability import metrics

        metrics.errors_total.labels(kind=type(exc).__name__).inc()
        return JSONResponse({"detail": str(exc)}, status_code=status_code)

    return handler


def create_app() -> FastAPI:
    """Build the FastAPI app (lifespan does the heavy initialisation)."""
    settings = get_settings()
    configure_logging(settings)

    # Interactive docs / OpenAPI schema are a development convenience; once
    # the API is directly internet-facing (mobile apps) they stay hidden
    # unless explicitly re-enabled via LEGAL_AI_API_DOCS_ENABLED.
    expose_docs = settings.env == "development" or settings.api_docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )
    app.state.audit_log = deque(maxlen=settings.audit_log_cap)

    # --- observability ---
    from backend.observability.tracing import setup_tracing

    setup_tracing(app, settings)

    # --- middleware (outermost added last) ---
    from fastapi.middleware.cors import CORSMiddleware

    from backend.api.middleware import AuditLogMiddleware, RateLimitMiddleware

    # CORS: lets the browser call the API directly (NEXT_PUBLIC_API_URL), which
    # is REQUIRED for real-time SSE — the Next.js /backend-api rewrite proxy
    # buffers the stream and delivers all frames at once at the end.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuditLogMiddleware)

    # --- exception handlers: domain errors -> HTTP codes ---
    app.add_exception_handler(AuthenticationError, _error_handler(401))
    app.add_exception_handler(AuthorizationError, _error_handler(403))
    app.add_exception_handler(UserAlreadyExistsError, _error_handler(400))
    app.add_exception_handler(QuotaExceededError, _error_handler(429))
    app.add_exception_handler(RateLimitError, _error_handler(429))
    app.add_exception_handler(GuardrailViolation, _error_handler(400))
    app.add_exception_handler(LegalAIError, _error_handler(500))

    # --- root probes ---
    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe: process is up."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        """Readiness: live per-dependency probes, not just attribute presence.

        Always 200 with {status, checks} — EXCEPT in strict infra mode
        (production), where a failed critical dependency yields HTTP 503 so
        orchestrators stop routing traffic. The critical set is configurable
        via ``strict_critical_components`` (default: milvus, postgres,
        database_probe, llm, embeddings, user_store); a failed
        vector_store_probe also counts while milvus is enabled.
        """
        ctx = getattr(request.app.state, "ctx", None)
        if ctx is None or getattr(request.app.state, "graph", None) is None:
            return JSONResponse(
                {"status": "not_ready", "checks": {"context": "missing", "graph": "missing"}},
                status_code=200,
            )

        checks: dict[str, Any] = dict(ctx.infra_status)

        # Live cheap probes against the configured backends.
        try:
            await ctx.cache.set("ready:probe", 1, ttl=5)
            checks["cache_probe"] = "ok" if await ctx.cache.get("ready:probe") == 1 else "degraded: probe failed"
        except Exception:
            checks["cache_probe"] = "degraded: probe failed"
        try:
            await ctx.vector_store.count()  # type: ignore[union-attr]
            checks["vector_store_probe"] = "ok"
        except Exception:
            checks["vector_store_probe"] = "degraded: probe failed"
        from backend.users.service import probe_database

        checks["database_probe"] = "ok" if await probe_database(ctx.settings) else "degraded: probe failed"

        def _down(*names: str) -> bool:
            return any(str(checks.get(name, "")).startswith("degraded") for name in names)

        degraded = any(str(v).startswith("degraded") for v in checks.values())
        critical_down = _down(*ctx.settings.strict_critical_list) or (
            ctx.settings.milvus_enabled and _down("vector_store_probe")
        )
        status = "degraded" if degraded else "ready"
        code = 503 if (ctx.settings.strict_infra_enabled and critical_down) else 200
        return JSONResponse({"status": status, "checks": checks}, status_code=code)

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus_metrics() -> PlainTextResponse:
        """Prometheus scrape endpoint (text exposition format)."""
        try:
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        except ImportError:
            return PlainTextResponse("# prometheus_client not installed\n")
        return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)

    # --- routers under /api/v1 ---
    from backend.api.routers import admin, articles, auth, billing, bookmarks, chat, citations, documents, draft, export, freshness, legal, models, push, search, share, sources, usage

    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(articles.router, prefix="/api/v1")
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(billing.router, prefix="/api/v1")
    app.include_router(bookmarks.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(citations.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(draft.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(freshness.router, prefix="/api/v1")
    app.include_router(legal.router, prefix="/api/v1")
    app.include_router(models.router, prefix="/api/v1")
    app.include_router(push.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")
    app.include_router(share.router, prefix="/api/v1")
    app.include_router(sources.router, prefix="/api/v1")
    app.include_router(usage.router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8000, reload=True)
