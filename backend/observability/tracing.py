"""OpenTelemetry tracing setup.

Entirely optional: when `settings.otel_enabled` is False (or the OTel
packages are missing) `setup_tracing` is a no-op. All OTel imports are lazy.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.core.config import Settings

logger = logging.getLogger(__name__)


def setup_tracing(app: Any, settings: Settings) -> None:
    """Instrument `app` with OTel + OTLP HTTP exporter when enabled."""
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception as exc:  # pragma: no cover - dependency optional
        logger.warning("otel_enabled but OpenTelemetry packages unavailable: %s", exc)
        return

    provider = TracerProvider(resource=Resource.create({"service.name": settings.app_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{settings.otel_endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("otel tracing enabled", extra={"otel_endpoint": settings.otel_endpoint})
