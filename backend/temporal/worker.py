"""Temporal worker entrypoint.

    python -m backend.temporal.worker

Starts a worker on `settings.temporal_task_queue` when
`settings.temporal_enabled` is true; otherwise prints a clear message and
exits 0.
"""

from __future__ import annotations

import asyncio

from backend.core.config import get_settings
from backend.temporal.client import get_temporal_client


async def _run_worker() -> int:
    settings = get_settings()
    if not settings.temporal_enabled:
        print(
            "Temporal is disabled (temporal_enabled=false / LEGAL_AI_TEMPORAL_ENABLED "
            "not set). Worker not started."
        )
        return 0

    try:
        from temporalio.worker import Worker
    except ImportError:
        print("The 'temporalio' package is not installed; cannot start the worker.")
        return 1

    client = await get_temporal_client(settings)
    if client is None:
        print(
            f"Temporal server unreachable at {settings.temporal_address} "
            f"(namespace '{settings.temporal_namespace}'). Worker not started."
        )
        return 1

    from backend.temporal.activities import ingest_path_activity, run_chat_turn_activity
    from backend.temporal.workflows import ConversationWorkflow, IngestionWorkflow

    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ConversationWorkflow, IngestionWorkflow],
        activities=[run_chat_turn_activity, ingest_path_activity],
    )
    print(f"Temporal worker listening on task queue '{settings.temporal_task_queue}' ...")
    await worker.run()
    return 0


def main() -> int:
    return asyncio.run(_run_worker())


if __name__ == "__main__":
    raise SystemExit(main())
