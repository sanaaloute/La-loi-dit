"""Celery app for background jobs (ingestion, freshness monitoring, evaluation).

The worker service in docker-compose runs:
    celery -A backend.workflows.celery_app worker --loglevel=INFO

Broker defaults to Redis (settings.celery_broker_url); without a broker the
tasks can still be invoked eagerly in-process (task.apply()) for tests/dev.
"""

from __future__ import annotations

import asyncio
from typing import Any

from celery import Celery

from backend.core.config import get_settings

settings = get_settings()

app = Celery(
    "legal_ai",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
)
app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=settings.celery_task_time_limit_seconds,
    worker_max_tasks_per_child=settings.celery_worker_max_tasks_per_child,
)


@app.task(name="legal_ai.ingest_document")
def ingest_document(path: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Background ingestion of one document into the retrieval stack."""
    from backend.core.context import build_context
    from backend.ingestion.pipeline import IngestionPipeline

    async def _run() -> dict[str, Any]:
        ctx = await build_context()
        result = await IngestionPipeline(ctx).ingest_path(path, **(metadata or {}))
        return result.model_dump(mode="json")

    return asyncio.run(_run())


@app.task(name="legal_ai.check_source_freshness")
def check_source_freshness() -> list[dict[str, Any]]:
    """Periodic knowledge-freshness sweep over official sources."""
    from backend.core.context import build_context
    from backend.ingestion.freshness import FreshnessMonitor

    async def _run() -> list[dict[str, Any]]:
        ctx = await build_context()
        events = await FreshnessMonitor(ctx).check_sources()
        return [e.model_dump(mode="json") if hasattr(e, "model_dump") else dict(e) for e in events]

    return asyncio.run(_run())


@app.task(name="legal_ai.run_evaluation")
def run_evaluation(dataset: str = "backend/evaluation/golden_dataset.json") -> dict[str, Any]:
    """Nightly evaluation over the golden dataset; returns the report summary."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "backend.evaluation.runner", "--dataset", dataset, "--out", "eval_report"],
        capture_output=True,
        text=True,
        timeout=settings.celery_evaluation_timeout_seconds,
    )
    return {"returncode": proc.returncode, "stdout_tail": proc.stdout[-2000:]}
