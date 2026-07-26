"""FastAPI application factory for the Burkina Faso Legal AI platform.

`create_app()` wires logging/metrics/tracing, middleware, exception handlers
and the /api/v1 routers. The heavy lifting (AppContext, compiled LangGraph
workflow, dev user store) happens in the lifespan, so importing this module
never boots external subsystems.

Run directly with:  python -m backend.api.main
"""

from __future__ import annotations

import logging
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
    RateLimitError,
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

    from backend.api.routers.auth import build_user_store

    app.state.user_store = build_user_store(ctx.settings)
    app.state.langfuse = None
    try:
        from backend.observability.langfuse_client import get_langfuse

        app.state.langfuse = get_langfuse(ctx.settings)
    except Exception:
        pass
    logger.info("application started", extra={"env": ctx.settings.env})
    yield
    app.state.graph = None
    app.state.ctx = None


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

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.audit_log = deque(maxlen=settings.audit_log_cap)

    # --- observability ---
    from backend.observability.tracing import setup_tracing

    setup_tracing(app, settings)

    # --- middleware (outermost added last) ---
    from backend.api.middleware import AuditLogMiddleware, RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuditLogMiddleware)

    # --- exception handlers: domain errors -> HTTP codes ---
    app.add_exception_handler(AuthenticationError, _error_handler(401))
    app.add_exception_handler(AuthorizationError, _error_handler(403))
    app.add_exception_handler(RateLimitError, _error_handler(429))
    app.add_exception_handler(GuardrailViolation, _error_handler(400))
    app.add_exception_handler(LegalAIError, _error_handler(500))

    # --- root probes ---
    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe: process is up."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> dict[str, Any]:
        """Readiness report: checks every context piece; always HTTP 200."""
        ctx = getattr(request.app.state, "ctx", None)
        checks = {
            "context": ctx is not None,
            "graph": getattr(request.app.state, "graph", None) is not None,
            "llm": getattr(ctx, "llm", None) is not None,
            "cache": getattr(ctx, "cache", None) is not None,
            "embedder": getattr(ctx, "embedder", None) is not None,
            "vector_store": getattr(ctx, "vector_store", None) is not None,
            "retriever": getattr(ctx, "retriever", None) is not None,
            "memory": getattr(ctx, "memory", None) is not None,
        }
        return {"status": "ready" if all(checks.values()) else "degraded", "checks": checks}

    @app.get("/metrics", response_class=PlainTextResponse)
    async def prometheus_metrics() -> PlainTextResponse:
        """Prometheus scrape endpoint (text exposition format)."""
        try:
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        except ImportError:
            return PlainTextResponse("# prometheus_client not installed\n")
        return PlainTextResponse(generate_latest().decode("utf-8"), media_type=CONTENT_TYPE_LATEST)

    # --- routers under /api/v1 ---
    from backend.api.routers import auth, chat, documents, export, search

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(chat.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(export.router, prefix="/api/v1")
    app.include_router(search.router, prefix="/api/v1")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.api.main:app", host="0.0.0.0", port=8000, reload=True)
