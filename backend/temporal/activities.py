"""Temporal activities wrapping the chat graph and the ingestion pipeline.

`temporalio` is imported lazily: without it, the activity functions remain
plain async callables (usable directly, e.g. in tests), just unregistered.
"""

from __future__ import annotations

from typing import Any

try:  # optional dependency
    from temporalio import activity

    _activity_defn = activity.defn
except Exception:  # pragma: no cover - exercised only when temporalio absent
    activity = None  # type: ignore[assignment]

    def _activity_defn(fn):  # type: ignore[misc]
        return fn

from backend.core.config import get_settings


@_activity_defn
async def run_chat_turn_activity(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one full agent-graph chat turn and return the serialized response.

    Payload keys: query (required), session_id, user_id, language,
    scenario_date (ISO string). A fresh offline-capable AppContext is built
    per invocation so the activity is self-contained and retry-safe.
    """
    from backend.core.context import build_context
    from backend.workflows.graph import build_graph, initial_state, run_query

    settings = get_settings()
    ctx = await build_context(settings)
    graph = build_graph(ctx)
    state = initial_state(
        payload["query"],
        session_id=payload.get("session_id"),
        user_id=payload.get("user_id", "anonymous"),
        language=payload.get("language"),
        scenario_date=payload.get("scenario_date"),
    )
    response = await run_query(graph, ctx, state)
    return response.model_dump(mode="json")


@_activity_defn
async def ingest_path_activity(path: str) -> list[dict[str, Any]]:
    """Ingest a file or directory through the ingestion pipeline.

    Returns the serialized DocumentIngestResult entries. The pipeline import
    stays lazy so this module loads while the ingestion subsystem is being
    built in parallel.
    """
    from backend.core.context import build_context
    from backend.ingestion.pipeline import IngestionPipeline

    settings = get_settings()
    ctx = await build_context(settings)
    pipeline = IngestionPipeline(ctx)
    results = await pipeline.ingest_path(path)
    return [r.model_dump(mode="json") if hasattr(r, "model_dump") else dict(r) for r in results]
